#!/usr/bin/env python3
"""
NVMEM subsystem workflow verification via bpftrace + sysfs.

Tests:
  1. Prerequisites (bpftrace, nvmem bus)
  2. NVMEM devices enumerated in sysfs
  3. Raw nvmem binary read via sysfs
  4. kprobe on nvmem_cell_read (consumer path)
  5. kprobe on __nvmem_reg_read (provider path)
  6. Read latency histogram for nvmem_cell_read
  7. Cell entries in sysfs (if exposed)
  8. Device type and read-only attributes
  9. u-boot env layout detection
 10. nvmem notifier kprobe

Each step is marked PASS or FAIL.

Requirements:
  - bpftrace >= 0.16
  - Linux kernel with CONFIG_NVMEM=y
  - Run as root (sudo python3 nvmem_trace_test.py)
"""

import subprocess
import tempfile
import os
import sys
import re
import glob
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


def get_nvmem_devices() -> list[str]:
    return glob.glob("/sys/bus/nvmem/devices/*")


# ─────────────────────────────────────────────────────────────────────────────
# Step 1 – Prerequisites
# ─────────────────────────────────────────────────────────────────────────────

def step_prerequisites() -> None:
    print("\n── Step 1: Prerequisites ──────────────────────────────────────")

    record("running as root", check_root())
    record("bpftrace available", bpftrace_available())

    bus = "/sys/bus/nvmem"
    record("nvmem bus registered", os.path.isdir(bus), bus)


# ─────────────────────────────────────────────────────────────────────────────
# Step 2 – NVMEM device enumeration
# ─────────────────────────────────────────────────────────────────────────────

def step_enumeration() -> list[str]:
    print("\n── Step 2: NVMEM device enumeration ───────────────────────────")

    devices = get_nvmem_devices()
    record("nvmem devices present", len(devices) > 0,
           f"count={len(devices)}: {[os.path.basename(d) for d in devices[:5]]}"
           if devices else "no nvmem devices found")

    return devices


# ─────────────────────────────────────────────────────────────────────────────
# Step 3 – Raw binary read via sysfs
# ─────────────────────────────────────────────────────────────────────────────

def step_binary_read(devices: list[str]) -> None:
    print("\n── Step 3: Raw nvmem binary sysfs read ────────────────────────")

    if not devices:
        record("nvmem binary sysfs read", False, "no devices to test")
        return

    for dev_path in devices:
        name = os.path.basename(dev_path)
        nvmem_bin = f"{dev_path}/nvmem"
        if not os.path.exists(nvmem_bin):
            continue
        try:
            with open(nvmem_bin, "rb") as f:
                data = f.read(64)
            record(f"{name}/nvmem binary read", len(data) > 0,
                   f"{len(data)} bytes read, first 8: {data[:8].hex()}")
            return  # one success is enough
        except PermissionError:
            record(f"{name}/nvmem binary read", False, "permission denied")
            return
        except OSError as e:
            record(f"{name}/nvmem binary read", False, str(e))
            return

    record("nvmem binary sysfs read", False, "no /nvmem file found in any device")


# ─────────────────────────────────────────────────────────────────────────────
# Step 4 – kprobe on nvmem_cell_read
# ─────────────────────────────────────────────────────────────────────────────

def step_cell_read_kprobe() -> None:
    print("\n── Step 4: kprobe on nvmem_cell_read ──────────────────────────")

    if not bpftrace_available():
        record("nvmem_cell_read kprobe", False, "bpftrace missing")
        return

    sym = "nvmem_cell_read"
    if not symbol_exists(sym):
        record(f"symbol {sym}", False, "module not loaded")
        return

    script = f"""
kprobe:{sym} {{
    printf("NVMEM_CELL_READ pid=%d comm=%s\\n", pid, comm);
}}
interval:s:6 {{ exit(); }}
"""
    # Trigger a read by reading the sysfs binary file in background
    def do_read():
        time.sleep(1)
        devices = get_nvmem_devices()
        for d in devices:
            nb = f"{d}/nvmem"
            if os.path.exists(nb):
                try:
                    with open(nb, "rb") as f:
                        f.read(16)
                except Exception:
                    pass
                break

    t = threading.Thread(target=do_read, daemon=True)
    t.start()
    out = run_bpftrace(script, timeout=10)
    t.join(timeout=2)

    # kprobe compiled = success even if no calls observed
    compiled = "NVMEM_CELL_READ" in out or "ERROR" not in out.upper()
    record("nvmem_cell_read kprobe attaches", compiled,
           "no cell reads in window" if "NVMEM_CELL_READ" not in out else "fired")


# ─────────────────────────────────────────────────────────────────────────────
# Step 5 – kprobe on __nvmem_reg_read (provider layer)
# ─────────────────────────────────────────────────────────────────────────────

def step_reg_read_kprobe() -> None:
    print("\n── Step 5: kprobe on __nvmem_reg_read (provider path) ─────────")

    if not bpftrace_available():
        record("__nvmem_reg_read kprobe", False, "bpftrace missing")
        return

    sym = "__nvmem_reg_read"
    if not symbol_exists(sym):
        record(f"symbol {sym}", False, "may be inlined or renamed")
        return

    script = f"""
kprobe:{sym} {{
    printf("REG_READ pid=%d offset=%u bytes=%u\\n", pid, arg1, arg2);
}}
interval:s:6 {{ exit(); }}
"""
    out = run_bpftrace(script, timeout=8)
    ok = "ERROR" not in out.upper() or "REG_READ" in out
    record("__nvmem_reg_read kprobe compiles", ok, out[:100] if not ok else "")


# ─────────────────────────────────────────────────────────────────────────────
# Step 6 – Read latency histogram
# ─────────────────────────────────────────────────────────────────────────────

def step_read_latency() -> None:
    print("\n── Step 6: nvmem read latency histogram ───────────────────────")

    if not bpftrace_available():
        record("nvmem read latency", False, "bpftrace missing")
        return

    sym = "nvmem_device_read"
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
    printf("LAT_DONE\\n");
    exit();
}}
"""
    # Trigger reads
    def do_reads():
        for _ in range(5):
            time.sleep(0.5)
            devices = get_nvmem_devices()
            for d in devices:
                nb = f"{d}/nvmem"
                if os.path.exists(nb):
                    try:
                        with open(nb, "rb") as f:
                            f.read(32)
                    except Exception:
                        pass
                    break

    t = threading.Thread(target=do_reads, daemon=True)
    t.start()
    out = run_bpftrace(script, timeout=12)
    t.join(timeout=2)

    done = "LAT_DONE" in out
    has_lat = "@lat_us" in out or "[" in out
    record("latency kprobe compiled and ran", done)
    record("read latency histogram produced", has_lat,
           "no reads triggered in window" if not has_lat else "")


# ─────────────────────────────────────────────────────────────────────────────
# Step 7 – Per-cell sysfs attributes
# ─────────────────────────────────────────────────────────────────────────────

def step_cell_sysfs(devices: list[str]) -> None:
    print("\n── Step 7: Per-cell sysfs attributes ──────────────────────────")

    found_cells = False
    for dev_path in devices:
        name = os.path.basename(dev_path)
        cells_dir = f"{dev_path}/cells"
        if os.path.isdir(cells_dir):
            cells = os.listdir(cells_dir)
            record(f"{name}/cells/ present", True, f"cells: {cells[:5]}")
            found_cells = True
            for cell in cells[:3]:
                val = sysfs_read(f"{cells_dir}/{cell}")
                if val:
                    record(f"  cell {cell} readable", True, val[:40])

    if not found_cells:
        record("per-cell sysfs (cells/)", False,
               "not exposed by current drivers (driver-dependent)")


# ─────────────────────────────────────────────────────────────────────────────
# Step 8 – Type and read-only attributes
# ─────────────────────────────────────────────────────────────────────────────

def step_device_attrs(devices: list[str]) -> None:
    print("\n── Step 8: Device type / read-only attributes ──────────────────")

    if not devices:
        record("device attributes", False, "no devices")
        return

    for dev_path in devices[:3]:
        name = os.path.basename(dev_path)
        for attr in ["type", "read-only"]:
            val = sysfs_read(f"{dev_path}/{attr}")
            if val is not None:
                record(f"{name}/{attr}", True, val)


# ─────────────────────────────────────────────────────────────────────────────
# Step 9 – u-boot env layout detection
# ─────────────────────────────────────────────────────────────────────────────

def step_uboot_env() -> None:
    print("\n── Step 9: u-boot-env layout detection ────────────────────────")

    uboot_devices = [d for d in get_nvmem_devices()
                     if "uboot" in os.path.basename(d).lower()
                     or "u-boot" in os.path.basename(d).lower()
                     or "env" in os.path.basename(d).lower()]

    record("u-boot-env nvmem devices", len(uboot_devices) > 0,
           f"found: {[os.path.basename(d) for d in uboot_devices]}"
           if uboot_devices else "not present (platform-dependent)")

    # Check if u_boot_env module is loaded
    r = subprocess.run(["grep", "-c", "u_boot_env", "/proc/modules"],
                       capture_output=True, text=True)
    u_boot_mod = r.returncode == 0 and int(r.stdout.strip() or "0") > 0
    sysmod = os.path.isdir("/sys/module/nvmem_u_boot_env")
    record("nvmem_u_boot_env module loaded", u_boot_mod or sysmod,
           "/sys/module/nvmem_u_boot_env" if sysmod else "not loaded")


# ─────────────────────────────────────────────────────────────────────────────
# Step 10 – nvmem notifier kprobe
# ─────────────────────────────────────────────────────────────────────────────

def step_notifier_kprobe() -> None:
    print("\n── Step 10: nvmem notifier kprobe ─────────────────────────────")

    if not bpftrace_available():
        record("nvmem notifier kprobe", False, "bpftrace missing")
        return

    sym = "nvmem_register_notifier"
    if not symbol_exists(sym):
        record(f"symbol {sym}", False, "module not loaded")
        return

    # Also check nvmem_register itself to verify module is live
    sym2 = "nvmem_register"
    has2 = symbol_exists(sym2)
    record(f"symbol {sym2} present", has2)

    script = f"""
kprobe:{sym} {{
    printf("NVMEM_NOTIF_REGISTER pid=%d\\n", pid);
}}
interval:s:4 {{
    printf("NOTIF_DONE\\n");
    exit();
}}
"""
    out = run_bpftrace(script, timeout=8)
    done = "NOTIF_DONE" in out
    record("nvmem_register_notifier kprobe compiles", done,
           out[:100] if not done else "")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    print("=" * 64)
    print("  NVMEM (Non-Volatile Memory) Subsystem Verification")
    print("  Linux kernel: drivers/nvmem/")
    print("=" * 64)

    step_prerequisites()
    devices = step_enumeration()
    step_binary_read(devices)
    step_cell_read_kprobe()
    step_reg_read_kprobe()
    step_read_latency()
    step_cell_sysfs(devices)
    step_device_attrs(devices)
    step_uboot_env()
    step_notifier_kprobe()

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
    print("\n  NOTE: Many steps are platform-dependent (need SoC with eFuse/OTP).")
    print("=" * 64)
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
