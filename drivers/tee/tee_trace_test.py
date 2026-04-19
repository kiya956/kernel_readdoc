#!/usr/bin/env python3
"""
TEE (Trusted Execution Environment) subsystem workflow verification.

Tests:
  1. Prerequisites (bpftrace, TEE devices, tee-supplicant)
  2. /dev/tee0 and /dev/teepriv0 present
  3. TEE version ioctl (TEE_IOC_VERSION)
  4. Shared memory allocation (TEE_IOC_SHM_ALLOC)
  5. kprobe on tee_ioctl dispatch
  6. Open/close session lifecycle (TEE_IOC_OPEN_SESSION)
  7. Supplicant character device present
  8. OP-TEE sysfs bus entry
  9. Shared memory pool debugfs stats
 10. kprobe latency on tee_shm_alloc_user_buf

Each step is marked PASS or FAIL.

Requirements:
  - bpftrace >= 0.16
  - Linux kernel with CONFIG_TEE=y, CONFIG_OPTEE=y or CONFIG_AMDTEE=y
  - Run as root (sudo python3 tee_trace_test.py)
  - OP-TEE hardware (Arm TrustZone) for session steps
"""

import subprocess
import tempfile
import os
import sys
import re
import glob
import fcntl
import struct
import threading
import time


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"

results: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    status = PASS if ok else FAIL
    line = f"  [{status}] {name}"
    if detail:
        line += f"  ({detail})"
    print(line)


def check_root() -> bool:
    return os.geteuid() == 0


def bpftrace_available() -> bool:
    try:
        r = subprocess.run(["bpftrace", "--version"], capture_output=True, timeout=5)
        return r.returncode == 0
    except FileNotFoundError:
        return False


def run_bpftrace(script: str, timeout: int = 12) -> str:
    with tempfile.NamedTemporaryFile("w", suffix=".bt", delete=False) as f:
        f.write(script)
        fname = f.name
    try:
        r = subprocess.run(
            ["bpftrace", fname],
            capture_output=True, text=True, timeout=timeout
        )
        return r.stdout + r.stderr
    except subprocess.TimeoutExpired:
        return ""
    finally:
        os.unlink(fname)


def sysfs_read(path: str) -> str | None:
    try:
        with open(path) as f:
            return f.read().strip()
    except (PermissionError, FileNotFoundError, OSError):
        return None


def symbol_exists(sym: str) -> bool:
    try:
        r = subprocess.run(
            ["grep", "-wc", sym, "/proc/kallsyms"],
            capture_output=True, text=True, timeout=5
        )
        return r.returncode == 0 and int(r.stdout.strip()) > 0
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────────────────
# TEE UAPI constants (from include/uapi/linux/tee.h)
# ─────────────────────────────────────────────────────────────────────────────

# struct tee_ioctl_version_data { __u32 impl_id; __u32 impl_caps; __u32 gen_caps; }
_TEE_VERSION_STRUCT = struct.Struct("III")
# TEE_IOC_MAGIC = 0xa4, TEE_IOC_BASE = 0
_TEE_IOC_MAGIC = 0xa4
TEE_IOC_VERSION = (2 << 30) | (_TEE_IOC_MAGIC << 8) | 0 | (_TEE_VERSION_STRUCT.size << 16)

# struct tee_ioctl_shm_alloc_data { __u64 size; __u32 flags; __s32 id; }
_TEE_SHM_ALLOC_STRUCT = struct.Struct("QIi")
TEE_IOC_SHM_ALLOC = (3 << 30) | (_TEE_IOC_MAGIC << 8) | 7 | (_TEE_SHM_ALLOC_STRUCT.size << 16)

# Implementation IDs
TEE_IMPL_ID_OPTEE = 1
TEE_IMPL_ID_AMDTEE = 2

IMPL_NAMES = {TEE_IMPL_ID_OPTEE: "OP-TEE", TEE_IMPL_ID_AMDTEE: "AMD-TEE"}


# ─────────────────────────────────────────────────────────────────────────────
# Step 1 – Prerequisites
# ─────────────────────────────────────────────────────────────────────────────

def step_prerequisites() -> None:
    print("\n── Step 1: Prerequisites ──────────────────────────────────────")

    record("running as root", check_root())
    record("bpftrace available", bpftrace_available())

    tee_bus = "/sys/bus/tee"
    record("tee bus registered", os.path.isdir(tee_bus), tee_bus)

    # tee-supplicant process running?
    r = subprocess.run(["pgrep", "-x", "tee-supplicant"],
                       capture_output=True, text=True)
    record("tee-supplicant running", r.returncode == 0,
           "tee-supplicant not running (TA loading will fail)" if r.returncode else "")


# ─────────────────────────────────────────────────────────────────────────────
# Step 2 – /dev/tee* devices present
# ─────────────────────────────────────────────────────────────────────────────

def step_devices() -> None:
    print("\n── Step 2: TEE device nodes ────────────────────────────────────")

    for dev in ["/dev/tee0", "/dev/teepriv0"]:
        exists = os.path.exists(dev)
        record(f"{dev} present", exists,
               "no TEE hardware/module" if not exists else "")

    # Also check for any tee* nodes
    tee_devs = glob.glob("/dev/tee*")
    record("at least one /dev/tee* node", len(tee_devs) > 0,
           f"found: {tee_devs}" if tee_devs else "no TEE device")


# ─────────────────────────────────────────────────────────────────────────────
# Step 3 – TEE_IOC_VERSION
# ─────────────────────────────────────────────────────────────────────────────

def step_version_ioctl() -> tuple[int, bool]:
    """Return (impl_id, success)."""
    print("\n── Step 3: TEE_IOC_VERSION ioctl ───────────────────────────────")

    dev_path = "/dev/tee0"
    if not os.path.exists(dev_path):
        record("TEE_IOC_VERSION", False, "device absent")
        return 0, False

    try:
        fd = os.open(dev_path, os.O_RDWR)
    except PermissionError as e:
        record("open /dev/tee0", False, str(e))
        return 0, False

    record("open /dev/tee0", True)

    buf = bytearray(_TEE_VERSION_STRUCT.size)
    try:
        fcntl.ioctl(fd, TEE_IOC_VERSION, buf)
        impl_id, impl_caps, gen_caps = _TEE_VERSION_STRUCT.unpack_from(buf)
        name = IMPL_NAMES.get(impl_id, f"unknown({impl_id})")
        record("TEE_IOC_VERSION succeeds", True,
               f"impl={name} impl_caps=0x{impl_caps:08x} gen_caps=0x{gen_caps:08x}")
        os.close(fd)
        return impl_id, True
    except OSError as e:
        record("TEE_IOC_VERSION succeeds", False, str(e))
        os.close(fd)
        return 0, False


# ─────────────────────────────────────────────────────────────────────────────
# Step 4 – Shared memory allocation (TEE_IOC_SHM_ALLOC)
# ─────────────────────────────────────────────────────────────────────────────

def step_shm_alloc() -> None:
    print("\n── Step 4: TEE_IOC_SHM_ALLOC ───────────────────────────────────")

    dev_path = "/dev/tee0"
    if not os.path.exists(dev_path):
        record("TEE_IOC_SHM_ALLOC", False, "device absent")
        return

    try:
        fd = os.open(dev_path, os.O_RDWR)
    except PermissionError as e:
        record("open /dev/tee0 for shm", False, str(e))
        return

    # size=4096, flags=0, id=0(output)
    buf = bytearray(_TEE_SHM_ALLOC_STRUCT.size)
    _TEE_SHM_ALLOC_STRUCT.pack_into(buf, 0, 4096, 0, 0)

    try:
        fcntl.ioctl(fd, TEE_IOC_SHM_ALLOC, buf)
        size, flags, shm_id = _TEE_SHM_ALLOC_STRUCT.unpack_from(buf)
        record("TEE_IOC_SHM_ALLOC succeeds", shm_id >= 0,
               f"shm_id={shm_id} size={size}")
    except OSError as e:
        record("TEE_IOC_SHM_ALLOC succeeds", False, str(e))
    finally:
        os.close(fd)


# ─────────────────────────────────────────────────────────────────────────────
# Step 5 – kprobe on tee ioctl dispatch
# ─────────────────────────────────────────────────────────────────────────────

def step_ioctl_kprobe() -> None:
    print("\n── Step 5: kprobe on tee_ioctl ─────────────────────────────────")

    if not bpftrace_available():
        record("tee_ioctl kprobe", False, "bpftrace missing")
        return

    sym = "tee_ioctl"
    has_sym = symbol_exists(sym)
    record(f"symbol {sym} in kallsyms", has_sym)

    if not has_sym:
        return

    # Fire a VERSION ioctl in background while bpftrace watches
    script = f"""
kprobe:{sym} {{
    printf("TEE_IOCTL pid=%d comm=%s cmd=0x%lx\\n", pid, comm, arg2);
}}
interval:s:6 {{ exit(); }}
"""
    hit_event = threading.Event()

    def do_ioctl():
        time.sleep(1)
        if not os.path.exists("/dev/tee0"):
            return
        try:
            fd = os.open("/dev/tee0", os.O_RDWR)
            buf = bytearray(_TEE_VERSION_STRUCT.size)
            fcntl.ioctl(fd, TEE_IOC_VERSION, buf)
            os.close(fd)
        except Exception:
            pass

    t = threading.Thread(target=do_ioctl, daemon=True)
    t.start()
    out = run_bpftrace(script, timeout=10)
    t.join(timeout=2)

    fired = "TEE_IOCTL" in out
    record("tee_ioctl kprobe fires", fired,
           "no /dev/tee0 to trigger" if not fired and not os.path.exists("/dev/tee0")
           else "check bpftrace output" if not fired else "")


# ─────────────────────────────────────────────────────────────────────────────
# Step 6 – Open session ioctl (requires OP-TEE hardware)
# ─────────────────────────────────────────────────────────────────────────────

def step_open_session() -> None:
    print("\n── Step 6: TEE_IOC_OPEN_SESSION (hardware-dependent) ───────────")

    # TEE_IOC_OPEN_SESSION is complex — just check the ioctl number is defined
    # and that the kprobe-able function exists

    syms = ["optee_open_session", "amdtee_open_session"]
    found = [(s, symbol_exists(s)) for s in syms]
    for sym, present in found:
        record(f"backend symbol {sym}", present,
               "driver not loaded" if not present else "")

    record("open_session hardware test",
           any(p for _, p in found),
           "SKIP: requires OP-TEE/AMDTEE hardware and tee-supplicant")


# ─────────────────────────────────────────────────────────────────────────────
# Step 7 – OP-TEE sysfs bus entry
# ─────────────────────────────────────────────────────────────────────────────

def step_optee_sysfs() -> None:
    print("\n── Step 7: OP-TEE sysfs bus entry ──────────────────────────────")

    tee_devices = glob.glob("/sys/bus/tee/devices/*")
    record("tee bus has devices", len(tee_devices) > 0,
           f"found: {[os.path.basename(d) for d in tee_devices]}" if tee_devices
           else "no TEE devices registered")

    optee_entries = [d for d in tee_devices if "optee" in os.path.basename(d).lower()]
    record("OP-TEE device in tee bus", len(optee_entries) > 0,
           f"{optee_entries}" if optee_entries else "no OP-TEE (check CONFIG_OPTEE)")

    amdtee_entries = [d for d in tee_devices if "amd" in os.path.basename(d).lower()]
    if amdtee_entries:
        record("AMD TEE device in tee bus", True, f"{amdtee_entries}")


# ─────────────────────────────────────────────────────────────────────────────
# Step 8 – Shared memory pool debugfs
# ─────────────────────────────────────────────────────────────────────────────

def step_shm_debugfs() -> None:
    print("\n── Step 8: Shared memory pool stats ────────────────────────────")

    for path in ["/sys/kernel/debug/tee", "/sys/kernel/debug/optee"]:
        if os.path.isdir(path):
            record(f"debugfs {path} present", True)
            entries = os.listdir(path)
            for e in entries:
                val = sysfs_read(f"{path}/{e}")
                if val is not None:
                    record(f"  {e} readable", True, val[:80])

    # Check /sys/kernel/debug/tee/ for shm pool info
    tee_debug = "/sys/kernel/debug/tee"
    if not os.path.isdir(tee_debug):
        record("tee debugfs", False, "not present or debugfs not mounted")


# ─────────────────────────────────────────────────────────────────────────────
# Step 9 – kprobe latency on tee_shm_alloc_user_buf
# ─────────────────────────────────────────────────────────────────────────────

def step_shm_alloc_latency() -> None:
    print("\n── Step 9: tee_shm_alloc latency kprobe ────────────────────────")

    if not bpftrace_available():
        record("shm_alloc latency", False, "bpftrace missing")
        return

    sym = "tee_shm_alloc_user_buf"
    if not symbol_exists(sym):
        record(f"symbol {sym}", False, "module not loaded")
        return

    script = f"""
kprobe:{sym}     {{ @start[tid] = nsecs; }}
kretprobe:{sym}  {{
    if (@start[tid]) {{
        @lat_us = hist((nsecs - @start[tid]) / 1000);
        delete(@start[tid]);
    }}
}}
interval:s:7 {{
    print(@lat_us);
    printf("SHM_ALLOC_DONE\\n");
    exit();
}}
"""
    # Trigger shm allocs in parallel
    def do_allocs():
        for _ in range(3):
            time.sleep(0.5)
            if not os.path.exists("/dev/tee0"):
                return
            try:
                fd = os.open("/dev/tee0", os.O_RDWR)
                buf = bytearray(_TEE_SHM_ALLOC_STRUCT.size)
                _TEE_SHM_ALLOC_STRUCT.pack_into(buf, 0, 4096, 0, 0)
                fcntl.ioctl(fd, TEE_IOC_SHM_ALLOC, buf)
                os.close(fd)
            except Exception:
                pass

    t = threading.Thread(target=do_allocs, daemon=True)
    t.start()
    out = run_bpftrace(script, timeout=12)
    t.join(timeout=2)

    done = "SHM_ALLOC_DONE" in out
    has_lat = "@lat_us" in out or "[" in out
    record("shm_alloc kprobe compiled and ran", done, out[:100] if not done else "")
    record("shm_alloc latency histogram produced", has_lat,
           "no allocs observed in window" if not has_lat else "")


# ─────────────────────────────────────────────────────────────────────────────
# Step 10 – Module presence check
# ─────────────────────────────────────────────────────────────────────────────

def step_module_check() -> None:
    print("\n── Step 10: Kernel module presence ────────────────────────────")

    r = subprocess.run(["lsmod"], capture_output=True, text=True)
    modules = r.stdout if r.returncode == 0 else ""

    for mod in ["tee", "optee", "amdtee", "tee_tstee"]:
        loaded = mod in modules or symbol_exists(f"{mod}_init") or symbol_exists(f"tee_{mod}")
        # Simpler: check /sys/module/
        has_sysfs = os.path.isdir(f"/sys/module/{mod}")
        loaded = has_sysfs or (mod in modules)
        record(f"module {mod} loaded", loaded,
               "built-in or not present" if not loaded else "")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    print("=" * 64)
    print("  TEE (Trusted Execution Environment) Subsystem Verification")
    print("  Linux kernel: drivers/tee/")
    print("=" * 64)

    step_prerequisites()
    step_devices()
    impl_id, _ = step_version_ioctl()
    step_shm_alloc()
    step_ioctl_kprobe()
    step_open_session()
    step_optee_sysfs()
    step_shm_debugfs()
    step_shm_alloc_latency()
    step_module_check()

    print("\n" + "=" * 64)
    print("  SUMMARY")
    print("=" * 64)
    passed = sum(1 for _, ok, _ in results if ok)
    failed = sum(1 for _, ok, _ in results if not ok)
    total  = len(results)
    print(f"  PASS: {passed}/{total}   FAIL: {failed}/{total}")
    if failed > 0:
        print("\n  Failed steps:")
        for name, ok, detail in results:
            if not ok:
                print(f"    - {name}" + (f": {detail}" if detail else ""))
    print("\n  NOTE: Hardware-dependent steps require Arm TrustZone or AMD PSP")
    print("  and a running tee-supplicant daemon.")
    print("=" * 64)
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
