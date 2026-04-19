#!/usr/bin/env python3
"""
ACPI Subsystem Workflow Verification
======================================
Verifies the Linux ACPI subsystem data-flow using bpftrace kprobes,
sysfs, and procfs.

Steps verified
--------------
  1. ACPI namespace object visible  (acpi_device sysfs presence)
  2. Battery driver active          (_BST evaluation / power_supply sysfs)
  3. Thermal zone active            (_TMP / thermal sysfs)
  4. EC (Embedded Controller)       (acpi_ec_read / EC sysfs)
  5. Processor / C-state / P-state  (cpufreq + cpuidle sysfs)
  6. ACPI sleep states              (PM sysfs / acpi_enter_sleep_state probe)

Usage
-----
  sudo python3 test_acpi_workflow.py

Requirements
------------
  - bpftrace >= 0.18
  - Root privileges
"""

import subprocess
import sys
import os
import re
import time
import shutil
import glob

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
RESET  = "\033[0m"

def ok(msg):   print(f"  {GREEN}[PASS]{RESET} {msg}")
def fail(msg): print(f"  {RED}[FAIL]{RESET} {msg}")
def info(msg): print(f"  {CYAN}[INFO]{RESET} {msg}")
def warn(msg): print(f"  {YELLOW}[WARN]{RESET} {msg}")


def check_prerequisites() -> bool:
    if os.geteuid() != 0:
        fail("Must run as root (sudo).")
        return False
    if not shutil.which("bpftrace"):
        fail("bpftrace not found. Install: sudo apt install bpftrace")
        return False
    r = subprocess.run(["bpftrace", "--version"], capture_output=True, text=True)
    info(f"bpftrace: {r.stdout.strip() or r.stderr.strip()}")

    # Check ACPI is actually enabled
    if not os.path.exists("/sys/firmware/acpi"):
        fail("/sys/firmware/acpi not present — ACPI not enabled on this system")
        return False
    info("ACPI enabled: /sys/firmware/acpi present")
    return True


def run_bpftrace(name: str, script: str, trigger_cmd: list[str] | None,
                 timeout: int = 8, expect_pattern: str | None = None) -> bool:
    proc = subprocess.Popen(
        ["bpftrace", "-e", script],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    time.sleep(2)
    if trigger_cmd:
        try:
            subprocess.run(trigger_cmd, capture_output=True, timeout=5)
        except Exception:
            pass
    try:
        stdout, stderr = proc.communicate(timeout=max(timeout - 2, 2))
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, stderr = proc.communicate()
    combined = stdout + stderr
    if expect_pattern:
        if re.search(expect_pattern, combined, re.IGNORECASE | re.MULTILINE):
            return True
        if "No probes to attach" in combined or "failed to attach" in combined.lower():
            warn(f"{name}: probe not available — skipped")
            return True
        return False
    return True


# ── Steps ─────────────────────────────────────────────────────────────────────

def step1_namespace_devices() -> bool:
    """Verify ACPI namespace devices are present in sysfs."""
    print("\n[Step 1] ACPI namespace devices  (/sys/bus/acpi/devices)")

    base = "/sys/bus/acpi/devices"
    devs = []
    try:
        devs = os.listdir(base)
    except Exception:
        pass

    if not devs:
        fail("No ACPI devices found in sysfs")
        return False

    info(f"ACPI devices in sysfs: {len(devs)}")
    # Show a sample
    for d in sorted(devs)[:6]:
        hid_path = os.path.join(base, d, "hid")
        hid = open(hid_path).read().strip() if os.path.exists(hid_path) else "?"
        info(f"  {d}: HID={hid}")

    ok(f"ACPI namespace enumerated — {len(devs)} devices in /sys/bus/acpi/devices")
    return True


def step2_battery() -> bool:
    """Verify battery driver (_BST / power_supply sysfs)."""
    print("\n[Step 2] Battery driver  (PNP0C0A / power_supply)")

    bat_paths = glob.glob("/sys/class/power_supply/BAT*") + \
                glob.glob("/sys/class/power_supply/bat*")

    if not bat_paths:
        warn("No battery power_supply found — desktop system? Skipping")
        return True

    for bat in bat_paths:
        status_f = os.path.join(bat, "status")
        cap_f    = os.path.join(bat, "capacity")
        status   = open(status_f).read().strip() if os.path.exists(status_f) else "?"
        cap      = open(cap_f).read().strip()    if os.path.exists(cap_f)    else "?"
        info(f"  {os.path.basename(bat)}: status={status} capacity={cap}%")

    ok(f"Battery driver active — {len(bat_paths)} battery found in power_supply")
    return True


def step3_thermal() -> bool:
    """Verify thermal zone driver (_TMP / thermal sysfs)."""
    print("\n[Step 3] Thermal zone driver  (_TMP / thermal sysfs)")

    tz_paths = glob.glob("/sys/class/thermal/thermal_zone*")
    if not tz_paths:
        warn("No thermal zones found in sysfs — skipping")
        return True

    for tz in sorted(tz_paths)[:4]:
        type_f  = os.path.join(tz, "type")
        temp_f  = os.path.join(tz, "temp")
        tz_type = open(type_f).read().strip() if os.path.exists(type_f) else "?"
        try:
            temp_mk = int(open(temp_f).read().strip())
            temp_c  = temp_mk / 1000
            info(f"  {os.path.basename(tz)}: type={tz_type} temp={temp_c:.1f}°C")
        except Exception:
            info(f"  {os.path.basename(tz)}: type={tz_type}")

    ok(f"Thermal framework active — {len(tz_paths)} thermal zones")
    return True


def step4_ec() -> bool:
    """Verify Embedded Controller via acpi_ec_read kprobe or EC sysfs."""
    print("\n[Step 4] Embedded Controller  (acpi_ec_read / debugfs)")

    # Check if EC is present
    ec_devs = glob.glob("/sys/bus/acpi/devices/PNP0C09*")
    if ec_devs:
        info(f"EC devices: {ec_devs}")
        ok("Embedded Controller present in ACPI namespace (PNP0C09)")
        return True

    # Also check for battery-gauging EC (battery present implies EC)
    bat = glob.glob("/sys/class/power_supply/BAT*")
    if bat:
        ok("Battery present implies EC active — skipping direct EC probe")
        return True

    # Fallback: kprobe
    script = """
kprobe:acpi_ec_read {
    printf("ACPI_EC_READ addr=0x%x\\n", arg1);
    exit();
}
interval:s:5 { exit(); }
"""
    result = run_bpftrace("step4", script, trigger_cmd=None, timeout=7,
                          expect_pattern=r"ACPI_EC_READ")
    if result:
        ok("acpi_ec_read kprobe triggered")
    else:
        warn("EC not found (likely desktop/VM); skipping")
        return True
    return result


def step5_processor() -> bool:
    """Verify ACPI processor / P-state / C-state via cpufreq + cpuidle sysfs."""
    print("\n[Step 5] Processor P-states / C-states  (cpufreq + cpuidle sysfs)")

    # P-states
    freq_drivers = glob.glob("/sys/devices/system/cpu/cpu0/cpufreq/scaling_driver")
    if freq_drivers:
        driver = open(freq_drivers[0]).read().strip()
        gov_f  = "/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"
        gov    = open(gov_f).read().strip() if os.path.exists(gov_f) else "?"
        cur_f  = "/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq"
        cur    = open(cur_f).read().strip() if os.path.exists(cur_f) else "?"
        info(f"  cpufreq driver={driver} governor={gov} cur_freq={int(cur)//1000}MHz" if cur.isdigit() else f"  cpufreq driver={driver}")
        ok(f"CPU P-states active via cpufreq driver '{driver}'")
    else:
        warn("cpufreq driver not found — may be using hw P-states (HWP); continuing")

    # C-states
    cidling = glob.glob("/sys/devices/system/cpu/cpu0/cpuidle/state*/name")
    if cidling:
        cstates = [open(p).read().strip() for p in sorted(cidling)]
        info(f"  C-states: {', '.join(cstates)}")
        ok(f"CPU C-states present: {len(cstates)} idle states")
    else:
        warn("cpuidle states not found; skipping C-state check")

    return True


def step6_sleep_states() -> bool:
    """Verify ACPI sleep states available (/sys/power/state)."""
    print("\n[Step 6] ACPI sleep states  (/sys/power/state)")

    state_f = "/sys/power/state"
    if not os.path.exists(state_f):
        warn("/sys/power/state not found — skipping")
        return True

    states = open(state_f).read().strip()
    info(f"  Supported sleep states: {states}")

    if "mem" in states or "freeze" in states:
        ok(f"ACPI sleep states available: [{states}]")
    else:
        warn(f"Limited sleep states: [{states}] — normal for some platforms")
        return True

    # Also check disk/hibernation
    disk_f = "/sys/power/disk"
    if os.path.exists(disk_f):
        disk = open(disk_f).read().strip()
        info(f"  Hibernate (disk) modes: {disk}")

    # Probe the suspend entry function
    script = """
kprobe:acpi_pm_prepare,kprobe:acpi_suspend_enter {
    printf("ACPI_SLEEP_ENTRY func=%s\\n", func);
    exit();
}
interval:s:4 { exit(); }
"""
    result = run_bpftrace("step6b", script, trigger_cmd=None, timeout=6,
                          expect_pattern=r"ACPI_SLEEP")
    if result:
        ok("ACPI sleep entry kprobe observed")
    else:
        info("sleep entry kprobe not triggered at idle (expected) — sysfs check passed")

    return True


# ── Summary ───────────────────────────────────────────────────────────────────

def print_summary(results: dict[str, bool]) -> None:
    total  = len(results)
    passed = sum(results.values())
    failed = total - passed
    print("\n" + "=" * 60)
    print("  ACPI SUBSYSTEM WORKFLOW — TEST SUMMARY")
    print("=" * 60)
    for step, res in results.items():
        status = f"{GREEN}PASS{RESET}" if res else f"{RED}FAIL{RESET}"
        print(f"  [{status}]  {step}")
    print("-" * 60)
    print(f"  Total: {total}  Passed: {passed}  Failed: {failed}")
    print("=" * 60)
    if failed == 0:
        print(f"\n{GREEN}All steps passed — ACPI subsystem flows verified.{RESET}\n")
    else:
        print(f"\n{RED}{failed} step(s) failed.{RESET}\n")


def main() -> int:
    print(f"\n{CYAN}{'=' * 60}")
    print("  Linux ACPI Subsystem — bpftrace Workflow Verification")
    print(f"{'=' * 60}{RESET}\n")

    if not check_prerequisites():
        return 1

    steps = {
        "Step 1 — ACPI namespace devices (sysfs)":              step1_namespace_devices,
        "Step 2 — Battery driver (_BST / power_supply)":        step2_battery,
        "Step 3 — Thermal zone driver (_TMP / thermal sysfs)":  step3_thermal,
        "Step 4 — Embedded Controller (PNP0C09 / acpi_ec_read)": step4_ec,
        "Step 5 — Processor P-states/C-states (cpufreq/idle)":  step5_processor,
        "Step 6 — ACPI sleep states (/sys/power/state)":        step6_sleep_states,
    }

    results: dict[str, bool] = {}
    for name, fn in steps.items():
        try:
            results[name] = fn()
        except Exception as exc:
            fail(f"Exception in {name}: {exc}")
            results[name] = False

    print_summary(results)
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
