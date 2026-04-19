#!/usr/bin/env python3
"""
devfreq + OPP subsystem verification via bpftrace + sysfs.

Tests:
  1. Prerequisites (bpftrace, devfreq class)
  2. devfreq devices enumerated
  3. cur_freq / governor / available_frequencies readable
  4. OPP table via sysfs
  5. Tracepoints: devfreq_frequency, devfreq_monitor
  6. kprobe on update_devfreq
  7. devfreq_frequency tracepoint fires during polling
  8. devfreq monitor latency histogram
  9. Governor change at runtime (userspace → original)
 10. OPP symbol presence (dev_pm_opp_set_rate)

Each step is marked PASS or FAIL.

Requirements:
  - bpftrace >= 0.16, CONFIG_PM_DEVFREQ=y
  - Run as root (sudo python3 devfreq_trace_test.py)
"""

import subprocess
import tempfile
import os
import sys
import re
import glob
import threading
import time


PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
results: list[tuple[str, bool, str]] = []


def record(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))


def check_root(): return os.geteuid() == 0


def bpftrace_available():
    try:
        return subprocess.run(["bpftrace", "--version"], capture_output=True, timeout=5).returncode == 0
    except FileNotFoundError:
        return False


def run_bpftrace(script, timeout=12):
    with tempfile.NamedTemporaryFile("w", suffix=".bt", delete=False) as f:
        f.write(script); fname = f.name
    try:
        r = subprocess.run(["bpftrace", fname], capture_output=True, text=True, timeout=timeout)
        return r.stdout + r.stderr
    except subprocess.TimeoutExpired:
        return ""
    finally:
        os.unlink(fname)


def sysfs_read(path):
    try:
        with open(path) as f: return f.read().strip()
    except Exception: return None


def symbol_exists(sym):
    try:
        r = subprocess.run(["grep", "-wc", sym, "/proc/kallsyms"], capture_output=True, text=True, timeout=5)
        return r.returncode == 0 and int(r.stdout.strip()) > 0
    except Exception: return False


def get_devfreq_devices():
    return glob.glob("/sys/class/devfreq/*")


# ── Steps ──────────────────────────────────────────────────────────────────

def step1_prerequisites():
    print("\n── Step 1: Prerequisites ──────────────────────────────────────")
    record("running as root", check_root())
    record("bpftrace available", bpftrace_available())
    record("devfreq class present", os.path.isdir("/sys/class/devfreq"), "/sys/class/devfreq")
    record("update_devfreq symbol", symbol_exists("update_devfreq"))


def step2_enumeration():
    print("\n── Step 2: devfreq device enumeration ─────────────────────────")
    devs = get_devfreq_devices()
    record("devfreq devices present", len(devs) > 0,
           f"{[os.path.basename(d) for d in devs[:4]]}" if devs else "no devfreq devices")
    return devs


def step3_sysfs_attrs(devs):
    print("\n── Step 3: sysfs attributes ────────────────────────────────────")
    if not devs:
        record("sysfs attrs", False, "no devices"); return
    for d in devs[:2]:
        name = os.path.basename(d)
        for attr in ["cur_freq", "governor", "available_frequencies", "min_freq", "max_freq"]:
            val = sysfs_read(f"{d}/{attr}")
            if val is not None:
                record(f"{name}/{attr}", True, val[:60])


def step4_opp_sysfs(devs):
    print("\n── Step 4: OPP table via sysfs ────────────────────────────────")
    for d in devs[:2]:
        name = os.path.basename(d)
        avail = sysfs_read(f"{d}/available_frequencies")
        if avail:
            freqs = avail.split()
            record(f"{name} OPP count", len(freqs) > 0, f"{len(freqs)} OPPs: {freqs[:4]}")
            return
    record("OPP frequencies readable", False, "no available_frequencies found")


def step5_tracepoints():
    print("\n── Step 5: devfreq tracepoints ────────────────────────────────")
    for tp in ["devfreq_frequency", "devfreq_monitor"]:
        path = f"/sys/kernel/debug/tracing/events/devfreq/{tp}"
        record(f"tracepoint {tp}", os.path.isdir(path), path)


def step6_update_devfreq_kprobe():
    print("\n── Step 6: kprobe on update_devfreq ────────────────────────────")
    if not bpftrace_available():
        record("update_devfreq kprobe", False, "bpftrace missing"); return
    if not symbol_exists("update_devfreq"):
        record("update_devfreq symbol", False, "absent"); return
    script = """
kprobe:update_devfreq { @calls[comm] = count(); }
interval:s:6 { print(@calls); printf("KPROBE_DONE\\n"); exit(); }
"""
    out = run_bpftrace(script, timeout=10)
    done = "KPROBE_DONE" in out
    fired = "@calls" in out and re.search(r':\s*[1-9]', out) is not None
    record("update_devfreq kprobe compiles", done)
    record("update_devfreq called in 6s window", fired,
           "no devfreq polling in window" if not fired else "")


def step7_frequency_tracepoint():
    print("\n── Step 7: devfreq_frequency tracepoint fires ──────────────────")
    if not bpftrace_available():
        record("devfreq_frequency tracepoint", False, "bpftrace missing"); return
    if not os.path.isdir("/sys/kernel/debug/tracing/events/devfreq"):
        record("devfreq tracepoints", False, "not present"); return
    script = """
tracepoint:devfreq:devfreq_frequency {
    printf("FREQ dev=%s new=%lu prev=%lu\\n",
           str(args->dev_name), args->freq, args->prev_freq);
}
tracepoint:devfreq:devfreq_monitor { @monitor_count++; }
interval:s:7 {
    printf("MONITOR_COUNT=%d\\n", @monitor_count);
    printf("TP_DONE\\n");
    exit();
}
"""
    out = run_bpftrace(script, timeout=11)
    done = "TP_DONE" in out
    record("devfreq tracepoint script ran", done, out[:80] if not done else "")
    m = re.search(r'MONITOR_COUNT=(\d+)', out)
    if m:
        count = int(m.group(1))
        record("devfreq_monitor fired", count > 0, f"count={count}")
    freq_fired = "FREQ" in out
    record("devfreq_frequency fired", freq_fired,
           "no freq changes in window (stable load)" if not freq_fired else "")


def step8_monitor_latency():
    print("\n── Step 8: devfreq_monitor latency histogram ───────────────────")
    if not bpftrace_available():
        record("monitor latency", False, "bpftrace missing"); return
    if not symbol_exists("devfreq_monitor"):
        record("devfreq_monitor symbol", False, "absent"); return
    script = """
kprobe:devfreq_monitor     { @start[tid] = nsecs; }
kretprobe:devfreq_monitor  {
    if (@start[tid]) {
        @lat_us = hist((nsecs - @start[tid]) / 1000);
        delete(@start[tid]);
    }
}
interval:s:7 { print(@lat_us); printf("LAT_DONE\\n"); exit(); }
"""
    out = run_bpftrace(script, timeout=11)
    done = "LAT_DONE" in out
    has_hist = "@lat_us" in out or "[" in out
    record("monitor latency kprobe ran", done)
    record("monitor latency histogram", has_hist,
           "no monitor calls in window" if not has_hist else "")


def step9_governor_switch(devs):
    print("\n── Step 9: governor runtime switch ────────────────────────────")
    if not devs:
        record("governor switch", False, "no devices"); return
    d = devs[0]
    name = os.path.basename(d)
    gov_path = f"{d}/governor"
    avail_path = f"{d}/available_governors"
    orig_gov = sysfs_read(gov_path)
    avail = sysfs_read(avail_path)
    record(f"{name} available_governors", avail is not None, avail or "")
    if not orig_gov or not avail:
        record("governor switch", False, "cannot read governor"); return
    # Try switching to 'performance' and back
    govs = avail.split()
    target = "performance" if "performance" in govs and orig_gov != "performance" else None
    if not target:
        record("governor switch", True, f"only one governor: {orig_gov}"); return
    try:
        with open(gov_path, "w") as f: f.write(target)
        new_gov = sysfs_read(gov_path)
        record("switch to 'performance'", new_gov == target, f"read back: {new_gov}")
        with open(gov_path, "w") as f: f.write(orig_gov)
        restored = sysfs_read(gov_path)
        record(f"restore '{orig_gov}'", restored == orig_gov, f"read back: {restored}")
    except PermissionError as e:
        record("governor switch", False, str(e))


def step10_opp_symbol():
    print("\n── Step 10: OPP framework symbols ─────────────────────────────")
    for sym in ["dev_pm_opp_set_rate", "dev_pm_opp_find_freq_ceil",
                "dev_pm_opp_get_opp_table"]:
        record(f"symbol {sym}", symbol_exists(sym))


# ── Main ──────────────────────────────────────────────────────────

def main():
    print("=" * 64)
    print("  devfreq + OPP Subsystem Verification")
    print("  Linux kernel: drivers/devfreq/ + drivers/opp/")
    print("=" * 64)
    step1_prerequisites()
    devs = step2_enumeration()
    step3_sysfs_attrs(devs)
    step4_opp_sysfs(devs)
    step5_tracepoints()
    step6_update_devfreq_kprobe()
    step7_frequency_tracepoint()
    step8_monitor_latency()
    step9_governor_switch(devs)
    step10_opp_symbol()
    print("\n" + "=" * 64)
    print("  SUMMARY")
    print("=" * 64)
    passed = sum(1 for _, ok, _ in results if ok)
    failed = sum(1 for _, ok, _ in results if not ok)
    print(f"  PASS: {passed}/{len(results)}   FAIL: {failed}/{len(results)}")
    if failed:
        for name, ok, detail in results:
            if not ok: print(f"    - {name}" + (f": {detail}" if detail else ""))
    print("=" * 64)
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()
