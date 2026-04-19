#!/usr/bin/env python3
"""
Interconnect (ICC) subsystem workflow verification via bpftrace + sysfs.

Tests:
  1. Prerequisites (bpftrace, ICC bus)
  2. ICC providers enumerated via debugfs
  3. interconnect_summary debugfs readable
  4. Trace events: icc_set_bw, icc_set_bw_end
  5. kprobe on icc_set_bw
  6. kprobe on icc_get / of_icc_get
  7. icc_set_bw latency histogram
  8. Provider nodes BW values
  9. icc-clk symbol presence
 10. Live bpftrace: watch icc_set_bw tracepoint

Each step is marked PASS or FAIL.

Requirements:
  - bpftrace >= 0.16
  - Linux kernel with CONFIG_INTERCONNECT=y
  - Run as root (sudo python3 icc_trace_test.py)
  - Qualcomm or other SoC with ICC provider for hardware steps
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


# ─────────────────────────────────────────────────────────────────────────────
# Step 1 – Prerequisites
# ─────────────────────────────────────────────────────────────────────────────

def step_prerequisites() -> None:
    print("\n── Step 1: Prerequisites ──────────────────────────────────────")

    record("running as root", check_root())
    record("bpftrace available", bpftrace_available())

    icc_debug = "/sys/kernel/debug/interconnect"
    record("interconnect debugfs dir", os.path.isdir(icc_debug), icc_debug)

    icc_sym = symbol_exists("icc_set_bw")
    record("icc_set_bw symbol in kallsyms", icc_sym,
           "CONFIG_INTERCONNECT not enabled" if not icc_sym else "")


# ─────────────────────────────────────────────────────────────────────────────
# Step 2 – Provider enumeration via debugfs
# ─────────────────────────────────────────────────────────────────────────────

def step_providers() -> None:
    print("\n── Step 2: ICC provider enumeration ───────────────────────────")

    providers_dir = "/sys/kernel/debug/interconnect/providers"
    if not os.path.isdir(providers_dir):
        # Some kernels put it directly under interconnect/
        alt = "/sys/kernel/debug/interconnect"
        entries = os.listdir(alt) if os.path.isdir(alt) else []
        providers = [e for e in entries if e not in ("interconnect_summary",)]
        record("ICC providers in debugfs", len(providers) > 0,
               f"found: {providers[:5]}" if providers else "no providers")
        return

    providers = os.listdir(providers_dir)
    record("ICC providers enumerated", len(providers) > 0,
           f"count={len(providers)}: {providers[:3]}"
           if providers else "no providers (no ICC hardware)")


# ─────────────────────────────────────────────────────────────────────────────
# Step 3 – interconnect_summary readable
# ─────────────────────────────────────────────────────────────────────────────

def step_summary() -> None:
    print("\n── Step 3: interconnect_summary debugfs ────────────────────────")

    summary_path = "/sys/kernel/debug/interconnect/interconnect_summary"
    if not os.path.exists(summary_path):
        record("interconnect_summary present", False, "no ICC hardware")
        return

    content = sysfs_read(summary_path)
    record("interconnect_summary readable", content is not None,
           f"{len(content or '')} bytes" if content else "empty or permission denied")

    if content:
        # Count lines = roughly number of ICC nodes
        lines = [l for l in content.splitlines() if l.strip() and "node" not in l.lower()]
        record("ICC nodes listed in summary", len(lines) > 0,
               f"~{len(lines)} node entries")


# ─────────────────────────────────────────────────────────────────────────────
# Step 4 – Trace events present
# ─────────────────────────────────────────────────────────────────────────────

def step_tracepoints() -> None:
    print("\n── Step 4: ICC tracepoints ─────────────────────────────────────")

    tp_base = "/sys/kernel/debug/tracing/events/interconnect"
    for tp in ["icc_set_bw", "icc_set_bw_end"]:
        path = f"{tp_base}/{tp}"
        record(f"tracepoint {tp}", os.path.isdir(path), path)


# ─────────────────────────────────────────────────────────────────────────────
# Step 5 – kprobe on icc_set_bw
# ─────────────────────────────────────────────────────────────────────────────

def step_icc_set_bw_kprobe() -> None:
    print("\n── Step 5: kprobe on icc_set_bw ───────────────────────────────")

    if not bpftrace_available():
        record("icc_set_bw kprobe", False, "bpftrace missing")
        return

    if not symbol_exists("icc_set_bw"):
        record("icc_set_bw kprobe", False, "symbol absent")
        return

    script = """
kprobe:icc_set_bw {
    printf("ICC_SET_BW pid=%d comm=%s avg=%u peak=%u\\n",
           pid, comm, arg1, arg2);
}
interval:s:6 { exit(); }
"""
    out = run_bpftrace(script, timeout=10)
    fired  = "ICC_SET_BW" in out
    ok     = "ERROR" not in out.upper() or fired
    record("icc_set_bw kprobe attaches", ok, out[:100] if not ok else "")
    record("icc_set_bw observed in 6s window", fired,
           "no BW changes in window (OK without active ICC)" if not fired else "")


# ─────────────────────────────────────────────────────────────────────────────
# Step 6 – kprobe on of_icc_get (path creation)
# ─────────────────────────────────────────────────────────────────────────────

def step_of_icc_get_kprobe() -> None:
    print("\n── Step 6: kprobe on of_icc_get ────────────────────────────────")

    if not bpftrace_available():
        record("of_icc_get kprobe", False, "bpftrace missing")
        return

    for sym in ["of_icc_get", "icc_get"]:
        if symbol_exists(sym):
            script = f"""
kprobe:{sym} {{
    printf("ICC_GET pid=%d comm=%s\\n", pid, comm);
}}
interval:s:4 {{
    printf("ICC_GET_DONE\\n");
    exit();
}}
"""
            out = run_bpftrace(script, timeout=8)
            done = "ICC_GET_DONE" in out
            record(f"{sym} kprobe compiles and runs", done,
                   out[:100] if not done else "")
            return

    record("of_icc_get / icc_get symbol", False, "both absent")


# ─────────────────────────────────────────────────────────────────────────────
# Step 7 – icc_set_bw latency histogram via tracepoint
# ─────────────────────────────────────────────────────────────────────────────

def step_latency() -> None:
    print("\n── Step 7: icc_set_bw latency histogram ────────────────────────")

    if not bpftrace_available():
        record("icc_set_bw latency", False, "bpftrace missing")
        return

    tp_dir = "/sys/kernel/debug/tracing/events/interconnect"
    if not os.path.isdir(tp_dir):
        record("ICC tracepoints for latency", False, "interconnect tracepoints absent")
        return

    script = """
tracepoint:interconnect:icc_set_bw     { @start[tid] = nsecs; }
tracepoint:interconnect:icc_set_bw_end {
    if (@start[tid]) {
        @lat_us = hist((nsecs - @start[tid]) / 1000);
        delete(@start[tid]);
    }
}
interval:s:7 {
    print(@lat_us);
    printf("LAT_DONE\\n");
    exit();
}
"""
    out = run_bpftrace(script, timeout=12)
    done    = "LAT_DONE" in out
    has_lat = "@lat_us" in out or "[" in out
    record("latency tracepoint script ran", done)
    record("icc_set_bw latency histogram produced", has_lat,
           "no BW events in window (ICC hardware needed)" if not has_lat else "")


# ─────────────────────────────────────────────────────────────────────────────
# Step 8 – Provider node BW values from debugfs
# ─────────────────────────────────────────────────────────────────────────────

def step_node_bw() -> None:
    print("\n── Step 8: Provider node BW values ────────────────────────────")

    summary = sysfs_read("/sys/kernel/debug/interconnect/interconnect_summary")
    if not summary:
        record("ICC node BW values", False, "summary not readable")
        return

    # Lines with non-zero BW
    nonzero = [l for l in summary.splitlines()
               if re.search(r'\b[1-9]\d*\s+[0-9]', l)]
    record("nodes with non-zero BW", len(nonzero) > 0,
           f"{len(nonzero)} active nodes" if nonzero else "all nodes at 0 BW (idle)")

    # Just report first few lines
    for line in summary.splitlines()[:5]:
        if line.strip():
            record(f"  summary line", True, line.strip()[:80])


# ─────────────────────────────────────────────────────────────────────────────
# Step 9 – icc-clk generic driver symbol
# ─────────────────────────────────────────────────────────────────────────────

def step_icc_clk() -> None:
    print("\n── Step 9: icc-clk generic driver ─────────────────────────────")

    sym = "icc_clk_register"
    has = symbol_exists(sym)
    record(f"icc_clk_register symbol present", has,
           "icc-clk.c driver built-in or loaded" if has else "not enabled")

    mod = os.path.isdir("/sys/module/icc_clk")
    if mod:
        record("icc_clk module loaded", True, "/sys/module/icc_clk")


# ─────────────────────────────────────────────────────────────────────────────
# Step 10 – Live tracepoint watch
# ─────────────────────────────────────────────────────────────────────────────

def step_live_watch() -> None:
    print("\n── Step 10: Live icc tracepoint watch (6s) ────────────────────")

    if not bpftrace_available():
        record("live ICC watch", False, "bpftrace missing")
        return

    tp_dir = "/sys/kernel/debug/tracing/events/interconnect"
    if not os.path.isdir(tp_dir):
        record("ICC tracepoints", False, "not present")
        return

    script = """
tracepoint:interconnect:icc_set_bw {
    @calls[comm] = count();
    @last_avg = args->avg_bw;
    @last_peak = args->peak_bw;
}
interval:s:6 {
    print(@calls);
    printf("WATCH_DONE avg=%u peak=%u\\n", @last_avg, @last_peak);
    exit();
}
"""
    out = run_bpftrace(script, timeout=10)
    done = "WATCH_DONE" in out
    record("ICC live watch ran", done, out[:100] if not done else "")

    calls_m = re.search(r'@calls\[([^\]]+)\]:\s*(\d+)', out)
    if calls_m:
        record(f"icc_set_bw caller: {calls_m.group(1)}", True,
               f"count={calls_m.group(2)}")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    print("=" * 64)
    print("  Interconnect (ICC) Subsystem Verification")
    print("  Linux kernel: drivers/interconnect/")
    print("=" * 64)

    step_prerequisites()
    step_providers()
    step_summary()
    step_tracepoints()
    step_icc_set_bw_kprobe()
    step_of_icc_get_kprobe()
    step_latency()
    step_node_bw()
    step_icc_clk()
    step_live_watch()

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
    print("\n  NOTE: Hardware steps require a Qualcomm/MediaTek/Samsung SoC")
    print("  with CONFIG_INTERCONNECT_QCOM or similar enabled.")
    print("=" * 64)
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
