#!/usr/bin/env python3
"""
test_dccp_subsystem.py — bpftrace verification of the DCCP subsystem.

DCCP (Datagram Congestion Control Protocol) provides unreliable datagrams
with congestion control, using a 3-way handshake and pluggable CCIDs.

Steps
-----
1.  Probe dccp_rcv                   — DCCP packet receive processing
2.  Probe dccp_sendmsg               — DCCP datagram send
3.  Probe dccp_connect               — initiate DCCP connection
4.  Probe dccp_v4_rcv                — IPv4 DCCP receive entry
5.  Probe dccp_create_openreq_child  — create child socket from request
6.  Probe dccp_close                 — DCCP connection teardown
7.  Probe dccp_init_sock             — DCCP socket initialization
8.  Probe dccp_setsockopt            — DCCP socket option handling
9.  Probe inet_dccp_listen           — DCCP listen state transition
10. Check /proc/net/dccp             — DCCP proc interface
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


print("\n=== DCCP (Datagram Congestion Control Protocol) bpftrace verification ===\n")

# ── Step 1: dccp_rcv ─────────────────────────────────────────────────────────
prog1 = """
kprobe:dccp_rcv {
    printf("HIT dccp_rcv\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(1, "dccp_rcv kprobe", prog1, timeout=8)

# ── Step 2: dccp_sendmsg ────────────────────────────────────────────────────
prog2 = """
kprobe:dccp_sendmsg {
    printf("HIT dccp_sendmsg\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(2, "dccp_sendmsg kprobe", prog2, timeout=8)

# ── Step 3: dccp_connect ────────────────────────────────────────────────────
prog3 = """
kprobe:dccp_connect {
    printf("HIT dccp_connect\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(3, "dccp_connect kprobe", prog3, timeout=8)

# ── Step 4: dccp_v4_rcv ─────────────────────────────────────────────────────
prog4 = """
kprobe:dccp_v4_rcv {
    printf("HIT dccp_v4_rcv\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(4, "dccp_v4_rcv kprobe", prog4, timeout=8)

# ── Step 5: dccp_create_openreq_child ────────────────────────────────────────
prog5 = """
kprobe:dccp_create_openreq_child {
    printf("HIT dccp_create_openreq_child\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(5, "dccp_create_openreq_child kprobe", prog5, timeout=8)

# ── Step 6: dccp_close ──────────────────────────────────────────────────────
prog6 = """
kprobe:dccp_close {
    printf("HIT dccp_close\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(6, "dccp_close kprobe", prog6, timeout=8)

# ── Step 7: dccp_init_sock ──────────────────────────────────────────────────
prog7 = """
kprobe:dccp_init_sock {
    printf("HIT dccp_init_sock\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(7, "dccp_init_sock kprobe", prog7, timeout=8)

# ── Step 8: dccp_setsockopt ─────────────────────────────────────────────────
prog8 = """
kprobe:dccp_setsockopt {
    printf("HIT dccp_setsockopt\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(8, "dccp_setsockopt kprobe", prog8, timeout=8)

# ── Step 9: inet_dccp_listen ────────────────────────────────────────────────
prog9 = """
kprobe:inet_dccp_listen {
    printf("HIT inet_dccp_listen\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(9, "inet_dccp_listen kprobe", prog9, timeout=8)

# ── Step 10: /proc/net/dccp ─────────────────────────────────────────────────
print(f"  Step 10: {'DCCP proc interface /proc/net/dccp':50s}", end=" ")
dccp_present = False
try:
    if os.path.exists("/proc/net/dccp"):
        dccp_present = True
    else:
        with open("/proc/kallsyms") as f:
            for line in f:
                if "dccp_rcv" in line or "dccp_v4_rcv" in line:
                    dccp_present = True
                    break
except Exception:
    pass
if dccp_present:
    print(PASS)
    results.append((10, "DCCP proc/kallsyms", PASS))
else:
    print(SKIP)
    results.append((10, "DCCP proc/kallsyms", SKIP))

# ── Summary ──────────────────────────────────────────────────────────────────
print()
passed = sum(1 for _, _, s in results if s == PASS)
failed = sum(1 for _, _, s in results if s == FAIL)
skipped = sum(1 for _, _, s in results if s == SKIP)
total = len(results)
print(f"Results: {passed}/{total} passed, {failed} failed, {skipped} skipped")
if failed > 0:
    sys.exit(1)
