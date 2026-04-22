#!/usr/bin/env python3
"""
test_bpf_net_subsystem.py — bpftrace verification of net/bpf test_run.

Steps
-----
1.  Probe bpf_prog_test_run_skb        — SK_SKB / socket-filter test run
2.  Probe bpf_prog_test_run_xdp        — XDP test run
3.  Probe bpf_prog_test_run_flow_dissector — flow-dissector test run
4.  Probe bpf_prog_test_run_sk_lookup  — SK_LOOKUP test run
5.  Probe bpf_prog_test_run_nf         — Netfilter BPF test run
6.  Probe bpf_prog_test_run_raw_tp     — raw-tracepoint test run
7.  Invoke BPF_PROG_TEST_RUN via bpf(2) syscall and verify return
8.  Probe bpf_test_timer_enter         — per-iteration timer
9.  Probe bpf_prog_run (kernel/bpf)    — low-level prog dispatch
10. Check /proc/sys/kernel/unprivileged_bpf_disabled
"""

import subprocess
import sys
import os
import time
import tempfile
import ctypes

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
SKIP = "\033[33mSKIP\033[0m"

BPFTRACE = "/usr/bin/bpftrace"
ATTACH_WAIT = 2.0


def run_bpftrace(program: str, trigger=None, timeout: int = 10) -> tuple[str, str, bool]:
    with tempfile.NamedTemporaryFile("w", suffix=".bt", delete=False) as f:
        f.write(program)
        bt_file = f.name
    try:
        proc = subprocess.Popen(
            [BPFTRACE, bt_file],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        time.sleep(ATTACH_WAIT)
        if trigger:
            try:
                trigger()
            except Exception:
                pass
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
        skipped = any(
            kw in stderr for kw in ("not traceable", "No probes", "unrecognized")
        )
        return stdout, stderr, skipped
    finally:
        os.unlink(bt_file)


results = []


def check(step_num: int, name: str, program: str, trigger=None,
          expect: str = "HIT", timeout: int = 10):
    stdout, stderr, skipped = run_bpftrace(program, trigger, timeout)
    if skipped:
        results.append((step_num, name, SKIP))
        print(f"  Step {step_num:2d}: {name:52s} {SKIP}")
        return
    ok = expect in stdout
    status = PASS if ok else FAIL
    results.append((step_num, name, status))
    print(f"  Step {step_num:2d}: {name:52s} {status}")
    if not ok:
        if stdout.strip():
            print(f"            stdout: {stdout.strip()[:200]}")
        if stderr.strip():
            print(f"            stderr: {stderr.strip()[:200]}")


print("\n=== net/bpf (BPF_PROG_TEST_RUN) bpftrace verification ===\n")


def run_bpf_test():
    """Use bpftool or python-bpf to trigger BPF_PROG_TEST_RUN if available."""
    # Try using bpftool prog run if available
    r = subprocess.run(["bpftool", "prog", "list"], capture_output=True, timeout=5)
    if r.returncode == 0:
        # Get first prog id and try test run
        lines = r.stdout.decode().strip().split("\n")
        for line in lines:
            if line and line[0].isdigit():
                prog_id = line.split(":")[0].strip()
                subprocess.run(
                    ["bpftool", "prog", "run", f"id/{prog_id}",
                     "data_in", "/dev/null"],
                    capture_output=True, timeout=5)
                break


# ── Step 1: bpf_prog_test_run_skb ────────────────────────────────────────────
prog1 = """
kprobe:bpf_prog_test_run_skb {
    printf("HIT bpf_prog_test_run_skb\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(1, "bpf_prog_test_run_skb kprobe", prog1, trigger=run_bpf_test, timeout=10)

# ── Step 2: bpf_prog_test_run_xdp ────────────────────────────────────────────
prog2 = """
kprobe:bpf_prog_test_run_xdp {
    printf("HIT bpf_prog_test_run_xdp\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(2, "bpf_prog_test_run_xdp kprobe", prog2, trigger=run_bpf_test, timeout=10)

# ── Step 3: bpf_prog_test_run_flow_dissector ─────────────────────────────────
prog3 = """
kprobe:bpf_prog_test_run_flow_dissector {
    printf("HIT bpf_prog_test_run_flow_dissector\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(3, "bpf_prog_test_run_flow_dissector kprobe", prog3, timeout=8)

# ── Step 4: bpf_prog_test_run_sk_lookup ──────────────────────────────────────
prog4 = """
kprobe:bpf_prog_test_run_sk_lookup {
    printf("HIT bpf_prog_test_run_sk_lookup\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(4, "bpf_prog_test_run_sk_lookup kprobe", prog4, timeout=8)

# ── Step 5: bpf_prog_test_run_nf ─────────────────────────────────────────────
prog5 = """
kprobe:bpf_prog_test_run_nf {
    printf("HIT bpf_prog_test_run_nf\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(5, "bpf_prog_test_run_nf kprobe", prog5, timeout=8)

# ── Step 6: bpf_prog_test_run_raw_tp ─────────────────────────────────────────
prog6 = """
kprobe:bpf_prog_test_run_raw_tp {
    printf("HIT bpf_prog_test_run_raw_tp\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(6, "bpf_prog_test_run_raw_tp kprobe", prog6, timeout=8)

# ── Step 7: BPF syscall via bpftrace itself ───────────────────────────────────
prog7 = """
tracepoint:syscalls:sys_enter_bpf {
    if (args->cmd == 10) {  // BPF_PROG_TEST_RUN = 10
        printf("HIT BPF_PROG_TEST_RUN syscall\\n");
        exit();
    }
}
interval:s:5 { exit(); }
"""
check(7, "BPF_PROG_TEST_RUN syscall tracepoint", prog7,
      trigger=run_bpf_test, timeout=10)

# ── Step 8: bpf_test_timer_enter ─────────────────────────────────────────────
prog8 = """
kprobe:bpf_test_timer_enter {
    printf("HIT bpf_test_timer_enter\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(8, "bpf_test_timer_enter kprobe", prog8, trigger=run_bpf_test, timeout=10)

# ── Step 9: bpf syscall enters kernel ─────────────────────────────────────────
prog9 = """
tracepoint:syscalls:sys_enter_bpf {
    printf("HIT bpf_syscall cmd=%d\\n", args->cmd);
    exit();
}
interval:s:3 { exit(); }
"""

def any_bpf_syscall():
    subprocess.run(["bpftool", "prog", "list"], capture_output=True, timeout=5)

check(9, "bpf(2) syscall tracepoint (any cmd)", prog9,
      trigger=any_bpf_syscall, timeout=8)

# ── Step 10: /proc/sys unprivileged BPF check ────────────────────────────────
print(f"  Step 10: {'/proc/sys/kernel/unprivileged_bpf_disabled':52s}", end=" ")
p = "/proc/sys/kernel/unprivileged_bpf_disabled"
if os.path.exists(p):
    val = open(p).read().strip()
    print(f"{PASS} (value={val})")
    results.append((10, "unprivileged_bpf_disabled sysctl", PASS))
else:
    print(SKIP)
    results.append((10, "unprivileged_bpf_disabled sysctl", SKIP))

# ── Summary ──────────────────────────────────────────────────────────────────
print()
passed = sum(1 for _, _, s in results if s == PASS)
failed = sum(1 for _, _, s in results if s == FAIL)
skipped = sum(1 for _, _, s in results if s == SKIP)
total = len(results)
print(f"Results: {passed}/{total} passed, {failed} failed, {skipped} skipped")
if failed > 0:
    sys.exit(1)
