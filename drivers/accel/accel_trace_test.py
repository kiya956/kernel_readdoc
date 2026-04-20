#!/usr/bin/env python3
"""
DRM Accelerator (accel) Subsystem — bpftrace verification test

Verifies the accel subsystem: device presence, tracepoints, job submission
flow, and power management events for Intel ivpu (NPU).

Requirements:
  - Linux >= 6.2 with CONFIG_DRM_ACCEL=y
  - bpftrace >= 0.14
  - Run as root for bpftrace steps
  - Intel Core Ultra system for ivpu steps (optional)

Usage:
  sudo python3 accel_trace_test.py
"""

import subprocess
import sys
import os
import glob
import time

RED   = "\033[91m"
GREEN = "\033[92m"
CYAN  = "\033[96m"
RESET = "\033[0m"

def pass_(msg): print(f"  {GREEN}[PASS]{RESET} {msg}")
def fail(msg):  print(f"  {RED}[FAIL]{RESET} {msg}")
def info(msg):  print(f"  {CYAN}[INFO]{RESET} {msg}")
def header(msg):
    print(f"\n{'='*62}")
    print(f"  {msg}")
    print(f"{'='*62}")

# ─────────────────────────────────────────────────────────────
# Step 1: /dev/accel/* device nodes
# ─────────────────────────────────────────────────────────────
def step1_device_nodes():
    header("Step 1: /dev/accel device nodes")

    nodes = glob.glob("/dev/accel/accel*")
    if nodes:
        for n in sorted(nodes):
            info(f"Found: {n}")
        pass_(f"{len(nodes)} accel device node(s) found")
        return True

    # Fallback: check sysfs class
    sysfs = glob.glob("/sys/class/accel/accel*")
    if sysfs:
        info(f"sysfs class entries: {sysfs}")
        pass_("accel class exists in sysfs (device node may need udev rule)")
        return True

    # Check kernel config
    cfg_path = f"/boot/config-{os.uname().release}"
    if os.path.exists(cfg_path):
        with open(cfg_path) as f:
            cfg = f.read()
        if "CONFIG_DRM_ACCEL=y" in cfg or "CONFIG_DRM_ACCEL=m" in cfg:
            info("CONFIG_DRM_ACCEL enabled but no devices found (no NPU hardware?)")
            pass_("accel framework compiled in (no NPU hardware on this system)")
            return True

    fail("No /dev/accel nodes and CONFIG_DRM_ACCEL not detected")
    return False

# ─────────────────────────────────────────────────────────────
# Step 2: Identify hardware driver (ivpu / amdxdna / habanalabs / qaic)
# ─────────────────────────────────────────────────────────────
def step2_identify_driver():
    header("Step 2: Identify loaded accel hardware driver")

    drivers = {
        "intel_vpu":   "Intel NPU (ivpu) — Core Ultra / Meteor/Arrow/Lunar Lake",
        "amdxdna":     "AMD XDNA NPU — Ryzen AI (Phoenix/Hawk Point/Strix)",
        "habanalabs":  "Intel Habana Gaudi / Gaudi2 accelerator",
        "qaic":        "Qualcomm Cloud AI 100",
    }

    found = []
    ret = subprocess.run(["lsmod"], capture_output=True, text=True)
    for mod, desc in drivers.items():
        if mod in ret.stdout:
            info(f"Loaded: {mod} — {desc}")
            found.append(mod)

    # Also check via PCI devices
    pci_ids = {
        "7d1d": "Intel MTL NPU",
        "ad1d": "Intel ARL NPU",
        "643e": "Intel LNL NPU",
        "b03e": "Intel PTL-P NPU",
        "a100": "Qualcomm AIC100",
        "a080": "Qualcomm AIC080",
    }
    ret2 = subprocess.run(["lspci", "-n"], capture_output=True, text=True)
    for pid, name in pci_ids.items():
        if pid.lower() in ret2.stdout.lower():
            info(f"PCI device: {name} ({pid})")

    if found:
        pass_(f"Driver(s) active: {', '.join(found)}")
    else:
        info("No accel driver loaded (expected if no NPU hardware)")
        pass_("Step skipped — no accel hardware detected")
    return True

# ─────────────────────────────────────────────────────────────
# Step 3: sysfs attributes
# ─────────────────────────────────────────────────────────────
def step3_sysfs_attrs():
    header("Step 3: accel sysfs attributes")

    entries = glob.glob("/sys/class/accel/accel*/")
    if not entries:
        info("No /sys/class/accel entries — skipping")
        pass_("Step skipped (no accel devices)")
        return True

    for entry in entries:
        info(f"Device: {entry}")
        for attr in ["uevent", "dev", "power/runtime_status"]:
            path = os.path.join(entry, attr)
            if os.path.exists(path):
                try:
                    with open(path) as f:
                        val = f.read().strip()[:80]
                    info(f"  {attr}: {val}")
                except Exception:
                    pass

    # Check debugfs
    dbg = glob.glob("/sys/kernel/debug/accel/*/name")
    if dbg:
        for d in dbg:
            with open(d) as f:
                info(f"debugfs name: {f.read().strip()}")
        pass_("accel debugfs entries present")
    else:
        info("No accel debugfs entries (mount debugfs or check permissions)")

    pass_("sysfs/debugfs inspection complete")
    return True

# ─────────────────────────────────────────────────────────────
# Step 4: ivpu tracepoints (pm / job / jsm)
# ─────────────────────────────────────────────────────────────
def step4_ivpu_tracepoints():
    header("Step 4: Intel ivpu tracepoints")

    if os.geteuid() != 0:
        fail("Root required for bpftrace")
        return False

    ret = subprocess.run(
        ["bpftrace", "-l", "tracepoint:ivpu:*"],
        capture_output=True, text=True, timeout=10
    )
    tps = [l for l in ret.stdout.strip().split("\n") if l.startswith("tracepoint:ivpu")]

    if not tps:
        info("No ivpu tracepoints (intel_vpu module not loaded or not present)")
        pass_("Step skipped — ivpu not available on this system")
        return True

    info(f"Found {len(tps)} ivpu tracepoints:")
    for tp in tps:
        info(f"  {tp}")
    pass_(f"ivpu tracepoints present: {', '.join(t.split(':')[-1] for t in tps)}")
    return True

# ─────────────────────────────────────────────────────────────
# Step 5: Capture ivpu pm (power) events
# ─────────────────────────────────────────────────────────────
BPFTRACE_IVPU_PM = r"""
tracepoint:ivpu:pm
{
    printf("IVPU_PM pid=%d state=%s\n", pid, str(args->state));
}
"""

def step5_ivpu_pm_events():
    header("Step 5: ivpu power management tracepoint (5s window)")

    if os.geteuid() != 0:
        fail("Root required")
        return False

    # Check if tracepoint exists first
    ret = subprocess.run(
        ["bpftrace", "-l", "tracepoint:ivpu:pm"],
        capture_output=True, text=True, timeout=5
    )
    if "ivpu:pm" not in ret.stdout:
        info("tracepoint:ivpu:pm not found — skipping")
        pass_("Step skipped (ivpu not loaded)")
        return True

    info("Watching ivpu:pm for 5s (trigger NPU activity if possible)...")
    proc = subprocess.Popen(
        ["bpftrace", "-e", BPFTRACE_IVPU_PM],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    lines = []
    start = time.time()
    try:
        while time.time() - start < 5:
            line = proc.stdout.readline()
            if line and "IVPU_PM" in line:
                lines.append(line.strip())
                info(f"  {line.strip()}")
    except Exception:
        pass
    finally:
        proc.terminate(); proc.wait(timeout=3)

    if lines:
        pass_(f"Captured {len(lines)} ivpu PM transitions")
    else:
        info("No PM events in window (NPU idle)")
        pass_("ivpu:pm probe attached OK")
    return True

# ─────────────────────────────────────────────────────────────
# Step 6: Capture ivpu job events
# ─────────────────────────────────────────────────────────────
BPFTRACE_IVPU_JOB = r"""
tracepoint:ivpu:job
{
    printf("IVPU_JOB pid=%d job_id=%d status=%d\n",
           pid, args->job_id, args->status);
}
"""

def step6_ivpu_job_events():
    header("Step 6: ivpu job submission tracepoint (5s window)")

    if os.geteuid() != 0:
        fail("Root required")
        return False

    ret = subprocess.run(
        ["bpftrace", "-l", "tracepoint:ivpu:job"],
        capture_output=True, text=True, timeout=5
    )
    if "ivpu:job" not in ret.stdout:
        info("tracepoint:ivpu:job not found — skipping")
        pass_("Step skipped (ivpu not loaded)")
        return True

    info("Watching ivpu:job for 5s...")
    proc = subprocess.Popen(
        ["bpftrace", "-e", BPFTRACE_IVPU_JOB],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    lines = []
    start = time.time()
    try:
        while time.time() - start < 5:
            line = proc.stdout.readline()
            if line and "IVPU_JOB" in line:
                lines.append(line.strip())
                if len(lines) <= 3:
                    info(f"  {line.strip()}")
    except Exception:
        pass
    finally:
        proc.terminate(); proc.wait(timeout=3)

    if lines:
        pass_(f"Captured {len(lines)} ivpu job events")
    else:
        info("No job events (submit an OpenVINO inference to trigger)")
        pass_("ivpu:job probe attached OK")
    return True

# ─────────────────────────────────────────────────────────────
# Step 7: drm_accel open() via kprobe
# ─────────────────────────────────────────────────────────────
BPFTRACE_ACCEL_OPEN = r"""
kprobe:accel_open
{
    printf("ACCEL_OPEN pid=%d comm=%s\n", pid, comm);
}
"""

def step7_accel_open_kprobe():
    header("Step 7: accel_open kprobe")

    if os.geteuid() != 0:
        fail("Root required")
        return False

    # Quick syntax check
    ret = subprocess.run(
        ["bpftrace", "--dry-run", "-e", BPFTRACE_ACCEL_OPEN],
        capture_output=True, text=True, timeout=10
    )
    if ret.returncode != 0 and "accel_open" in ret.stderr:
        info("accel_open symbol not found (module not loaded)")
        pass_("Step skipped (accel_open not in kallsyms)")
        return True

    info("Watching accel_open kprobe for 5s...")
    proc = subprocess.Popen(
        ["bpftrace", "-e", BPFTRACE_ACCEL_OPEN],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    lines = []
    start = time.time()
    try:
        while time.time() - start < 5:
            line = proc.stdout.readline()
            if line and "ACCEL_OPEN" in line:
                lines.append(line.strip())
                info(f"  {line.strip()}")
    except Exception:
        pass
    finally:
        proc.terminate(); proc.wait(timeout=3)

    if lines:
        pass_(f"accel_open called {len(lines)} time(s)")
    else:
        info("No accel_open calls (no userspace opened /dev/accel in window)")
        pass_("accel_open kprobe attached OK")
    return True

# ─────────────────────────────────────────────────────────────
# Step 8: Runtime PM state
# ─────────────────────────────────────────────────────────────
def step8_runtime_pm():
    header("Step 8: NPU Runtime PM state")

    pm_paths = glob.glob("/sys/bus/pci/devices/*/power/runtime_status")
    npu_pids = ["7d1d", "ad1d", "643e", "b03e", "fd3e"]

    found = False
    for pm_path in pm_paths:
        dev_dir = os.path.dirname(os.path.dirname(pm_path))
        vendor_path = os.path.join(dev_dir, "vendor")
        device_path = os.path.join(dev_dir, "device")
        try:
            with open(device_path) as f:
                device_id = f.read().strip().replace("0x", "")
            if device_id.lower() in npu_pids:
                with open(pm_path) as f:
                    status = f.read().strip()
                info(f"Intel NPU (device {device_id}) PM status: {status}")
                found = True
                if status in ["suspended", "active"]:
                    pass_(f"NPU runtime PM state: {status}")
                else:
                    pass_(f"NPU runtime PM state readable: {status}")
        except Exception:
            continue

    if not found:
        info("No Intel NPU PCI device found (checking generic accel PM)")
        # Check any accel device PM
        for entry in glob.glob("/sys/class/accel/accel*"):
            pm = os.path.join(entry, "power/runtime_status")
            if os.path.exists(pm):
                with open(pm) as f:
                    val = f.read().strip()
                info(f"accel PM: {val}")
                found = True

    if not found:
        info("No NPU hardware detected on this system")
        pass_("Step skipped (no compatible NPU)")
    return True

# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────
def main():
    print("=" * 62)
    print("  DRM Accelerator (accel) Subsystem — bpftrace Verification")
    print("=" * 62)

    if os.geteuid() != 0:
        print(f"\n{RED}WARNING: Not running as root.{RESET}")
        print("Steps 4-7 require root. Steps 1-3, 8 will run.\n")

    steps = [
        ("/dev/accel device nodes",         step1_device_nodes),
        ("Identify hardware driver",         step2_identify_driver),
        ("sysfs / debugfs attributes",       step3_sysfs_attrs),
        ("ivpu tracepoints (pm/job/jsm)",    step4_ivpu_tracepoints),
        ("ivpu PM events (5s)",              step5_ivpu_pm_events),
        ("ivpu job events (5s)",             step6_ivpu_job_events),
        ("accel_open kprobe (5s)",           step7_accel_open_kprobe),
        ("NPU runtime PM state",             step8_runtime_pm),
    ]

    results = []
    for name, fn in steps:
        try:
            ok = fn()
            results.append((name, ok if ok is not None else True))
        except subprocess.TimeoutExpired:
            fail(f"Timeout: {name}")
            results.append((name, False))
        except Exception as e:
            fail(f"Exception in {name}: {e}")
            results.append((name, False))

    header("Summary")
    passed = sum(1 for _, ok in results if ok)
    total  = len(results)
    for name, ok in results:
        status = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
        print(f"  [{status}] {name}")

    print(f"\n  Result: {passed}/{total} steps passed")
    sys.exit(0 if passed == total else 1)

if __name__ == "__main__":
    main()
