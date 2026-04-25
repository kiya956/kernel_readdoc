#!/usr/bin/env python3
"""
test_sctp_subsystem.py — bpftrace verification of the SCTP subsystem.

SCTP (Stream Control Transmission Protocol) is a reliable, message-oriented
transport with multi-homing and multi-streaming, using a 4-way handshake.

Steps
-----
1.  Probe sctp_rcv               — inbound SCTP packet receive
2.  Probe sctp_sendmsg           — outbound SCTP message send
3.  Probe sctp_do_sm             — SCTP state machine processing
4.  Probe sctp_association_new   — new SCTP association creation
5.  Probe sctp_endpoint_new      — new SCTP endpoint creation
6.  Probe sctp_connect           — initiate SCTP association (INIT)
7.  Probe sctp_bind              — bind SCTP endpoint to address
8.  Probe sctp_init_sock         — SCTP socket initialization
9.  Probe sctp_chunk_new         — allocate new SCTP chunk
10. Check /proc/net/sctp         — SCTP proc interface
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


print("\n=== SCTP (Stream Control Transmission Protocol) bpftrace verification ===\n")

# ── Step 1: sctp_rcv ─────────────────────────────────────────────────────────
prog1 = """
kprobe:sctp_rcv {
    printf("HIT sctp_rcv\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(1, "sctp_rcv kprobe", prog1, timeout=8)

# ── Step 2: sctp_sendmsg ────────────────────────────────────────────────────
prog2 = """
kprobe:sctp_sendmsg {
    printf("HIT sctp_sendmsg\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(2, "sctp_sendmsg kprobe", prog2, timeout=8)

# ── Step 3: sctp_do_sm ──────────────────────────────────────────────────────
prog3 = """
kprobe:sctp_do_sm {
    printf("HIT sctp_do_sm\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(3, "sctp_do_sm kprobe", prog3, timeout=8)

# ── Step 4: sctp_association_new ─────────────────────────────────────────────
prog4 = """
kprobe:sctp_association_new {
    printf("HIT sctp_association_new\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(4, "sctp_association_new kprobe", prog4, timeout=8)

# ── Step 5: sctp_endpoint_new ────────────────────────────────────────────────
prog5 = """
kprobe:sctp_endpoint_new {
    printf("HIT sctp_endpoint_new\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(5, "sctp_endpoint_new kprobe", prog5, timeout=8)

# ── Step 6: sctp_connect ────────────────────────────────────────────────────
prog6 = """
kprobe:sctp_connect {
    printf("HIT sctp_connect\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(6, "sctp_connect kprobe", prog6, timeout=8)

# ── Step 7: sctp_bind ───────────────────────────────────────────────────────
prog7 = """
kprobe:sctp_bind {
    printf("HIT sctp_bind\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(7, "sctp_bind kprobe", prog7, timeout=8)

# ── Step 8: sctp_init_sock ──────────────────────────────────────────────────
prog8 = """
kprobe:sctp_init_sock {
    printf("HIT sctp_init_sock\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(8, "sctp_init_sock kprobe", prog8, timeout=8)

# ── Step 9: sctp_chunk_new ──────────────────────────────────────────────────
prog9 = """
kprobe:sctp_chunk_new {
    printf("HIT sctp_chunk_new\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(9, "sctp_chunk_new kprobe", prog9, timeout=8)

# ── Step 10: /proc/net/sctp ─────────────────────────────────────────────────
print(f"  Step 10: {'SCTP proc interface /proc/net/sctp':50s}", end=" ")
sctp_present = False
try:
    if os.path.isdir("/proc/net/sctp"):
        sctp_present = True
    elif os.path.exists("/proc/net/sctp"):
        sctp_present = True
    else:
        with open("/proc/kallsyms") as f:
            for line in f:
                if "sctp_rcv" in line:
                    sctp_present = True
                    break
except Exception:
    pass
if sctp_present:
    print(PASS)
    results.append((10, "SCTP proc/kallsyms", PASS))
else:
    print(SKIP)
    results.append((10, "SCTP proc/kallsyms", SKIP))

# ── Summary ──────────────────────────────────────────────────────────────────
print()
passed = sum(1 for _, _, s in results if s == PASS)
failed = sum(1 for _, _, s in results if s == FAIL)
skipped = sum(1 for _, _, s in results if s == SKIP)
total = len(results)
print(f"Results: {passed}/{total} passed, {failed} failed, {skipped} skipped")
if failed > 0:
    sys.exit(1)
