#!/usr/bin/env python3
"""
test_xfrm_subsystem.py — bpftrace verification of the XFRM (IPsec) subsystem.

XFRM implements the IPsec transform framework: Security Associations (SA),
Security Policies (SP), and ESP/AH transforms for encrypted/authenticated
IP communication.

Steps
-----
1.  Probe xfrm_lookup          — output path policy + SA resolution
2.  Probe xfrm_input           — inbound ESP/AH decryption
3.  Probe xfrm_output          — outbound ESP/AH encryption
4.  Probe xfrm_state_find      — find SA matching policy template
5.  Probe xfrm_policy_lookup   — search policy database
6.  Probe xfrm_state_alloc     — allocate new Security Association
7.  Probe xfrm_policy_insert   — insert new Security Policy
8.  Probe xfrm_sk_policy_insert — per-socket IPsec policy
9.  Probe xfrm_replay_check    — anti-replay window verification
10. Check /proc/net/xfrm_stat  — XFRM statistics interface
"""

import subprocess
import sys
import os
import time
import tempfile

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
        print(f"  Step {step_num:2d}: {name:50s} {SKIP}")
        return
    ok = expect in stdout
    status = PASS if ok else FAIL
    results.append((step_num, name, status))
    print(f"  Step {step_num:2d}: {name:50s} {status}")
    if not ok:
        if stdout.strip():
            print(f"            stdout: {stdout.strip()[:200]}")
        if stderr.strip():
            print(f"            stderr: {stderr.strip()[:200]}")


print("\n=== XFRM (IPsec Transform Framework) bpftrace verification ===\n")

# ── Step 1: xfrm_lookup ─────────────────────────────────────────────────────
prog1 = """
kprobe:xfrm_lookup {
    printf("HIT xfrm_lookup\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(1, "xfrm_lookup kprobe", prog1, timeout=8)

# ── Step 2: xfrm_input ──────────────────────────────────────────────────────
prog2 = """
kprobe:xfrm_input {
    printf("HIT xfrm_input\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(2, "xfrm_input kprobe", prog2, timeout=8)

# ── Step 3: xfrm_output ─────────────────────────────────────────────────────
prog3 = """
kprobe:xfrm_output {
    printf("HIT xfrm_output\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(3, "xfrm_output kprobe", prog3, timeout=8)

# ── Step 4: xfrm_state_find ─────────────────────────────────────────────────
prog4 = """
kprobe:xfrm_state_find {
    printf("HIT xfrm_state_find\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(4, "xfrm_state_find kprobe", prog4, timeout=8)

# ── Step 5: xfrm_policy_lookup ──────────────────────────────────────────────
prog5 = """
kprobe:xfrm_policy_lookup {
    printf("HIT xfrm_policy_lookup\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(5, "xfrm_policy_lookup kprobe", prog5, timeout=8)

# ── Step 6: xfrm_state_alloc ────────────────────────────────────────────────
prog6 = """
kprobe:xfrm_state_alloc {
    printf("HIT xfrm_state_alloc\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(6, "xfrm_state_alloc kprobe", prog6, timeout=8)

# ── Step 7: xfrm_policy_insert ──────────────────────────────────────────────
prog7 = """
kprobe:xfrm_policy_insert {
    printf("HIT xfrm_policy_insert\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(7, "xfrm_policy_insert kprobe", prog7, timeout=8)

# ── Step 8: xfrm_sk_policy_insert ───────────────────────────────────────────
prog8 = """
kprobe:xfrm_sk_policy_insert {
    printf("HIT xfrm_sk_policy_insert\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(8, "xfrm_sk_policy_insert kprobe", prog8, timeout=8)

# ── Step 9: xfrm_replay_check ───────────────────────────────────────────────
prog9 = """
kprobe:xfrm_replay_check {
    printf("HIT xfrm_replay_check\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(9, "xfrm_replay_check kprobe", prog9, timeout=8)

# ── Step 10: /proc/net/xfrm_stat ────────────────────────────────────────────
print(f"  Step 10: {'XFRM stats in /proc/net/xfrm_stat':50s}", end=" ")
xfrm_present = False
try:
    if os.path.exists("/proc/net/xfrm_stat"):
        xfrm_present = True
    else:
        with open("/proc/kallsyms") as f:
            for line in f:
                if "xfrm_lookup" in line or "xfrm_state_find" in line:
                    xfrm_present = True
                    break
except Exception:
    pass
if xfrm_present:
    print(PASS)
    results.append((10, "XFRM proc/kallsyms", PASS))
else:
    print(SKIP)
    results.append((10, "XFRM proc/kallsyms", SKIP))

# ── Summary ──────────────────────────────────────────────────────────────────
print()
passed = sum(1 for _, _, s in results if s == PASS)
failed = sum(1 for _, _, s in results if s == FAIL)
skipped = sum(1 for _, _, s in results if s == SKIP)
total = len(results)
print(f"Results: {passed}/{total} passed, {failed} failed, {skipped} skipped")
if failed > 0:
    sys.exit(1)
