#!/usr/bin/env python3
"""
test_can_subsystem.py — bpftrace verification of the CAN subsystem.

Controller Area Network (AF_CAN) provides raw CAN, BCM, ISO-TP, and J1939
protocol layers over CAN bus interfaces (vcan, slcan, hardware adapters).

Steps
-----
1.  Probe can_rcv                — CAN frame receive path
2.  Probe can_send               — CAN frame transmit path
3.  Probe raw_rcv                — CAN_RAW socket receive callback
4.  Probe bcm_sendmsg            — BCM socket send handler
5.  Probe isotp_rcv              — ISO-TP receive / reassembly
6.  Probe can_rx_register        — register CAN ID receive filter
7.  Probe can_create             — AF_CAN socket creation
8.  Probe vcan_tx                — virtual CAN loopback transmit
9.  Probe j1939_send_one         — J1939 single-frame transmit
10. Check /proc/net/can          — CAN statistics interface
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


print("\n=== CAN (Controller Area Network) bpftrace verification ===\n")

# ── Step 1: can_rcv ──────────────────────────────────────────────────────────
prog1 = """
kprobe:can_rcv {
    printf("HIT can_rcv\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(1, "can_rcv kprobe", prog1, timeout=8)

# ── Step 2: can_send ─────────────────────────────────────────────────────────
prog2 = """
kprobe:can_send {
    printf("HIT can_send\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(2, "can_send kprobe", prog2, timeout=8)

# ── Step 3: raw_rcv ──────────────────────────────────────────────────────────
prog3 = """
kprobe:raw_rcv {
    printf("HIT raw_rcv\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(3, "raw_rcv kprobe", prog3, timeout=8)

# ── Step 4: bcm_sendmsg ─────────────────────────────────────────────────────
prog4 = """
kprobe:bcm_sendmsg {
    printf("HIT bcm_sendmsg\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(4, "bcm_sendmsg kprobe", prog4, timeout=8)

# ── Step 5: isotp_rcv ────────────────────────────────────────────────────────
prog5 = """
kprobe:isotp_rcv {
    printf("HIT isotp_rcv\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(5, "isotp_rcv kprobe", prog5, timeout=8)

# ── Step 6: can_rx_register ──────────────────────────────────────────────────
prog6 = """
kprobe:can_rx_register {
    printf("HIT can_rx_register\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(6, "can_rx_register kprobe", prog6, timeout=8)

# ── Step 7: can_create ───────────────────────────────────────────────────────
prog7 = """
kprobe:can_create {
    printf("HIT can_create\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(7, "can_create kprobe", prog7, timeout=8)

# ── Step 8: vcan_tx ──────────────────────────────────────────────────────────
prog8 = """
kprobe:vcan_tx {
    printf("HIT vcan_tx\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(8, "vcan_tx kprobe", prog8, timeout=8)

# ── Step 9: j1939_send_one ───────────────────────────────────────────────────
prog9 = """
kprobe:j1939_send_one {
    printf("HIT j1939_send_one\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(9, "j1939_send_one kprobe", prog9, timeout=8)

# ── Step 10: /proc/net/can ───────────────────────────────────────────────────
print(f"  Step 10: {'CAN stats in /proc/net/can or kallsyms':50s}", end=" ")
can_present = False
try:
    if os.path.exists("/proc/net/can"):
        can_present = True
    else:
        with open("/proc/kallsyms") as f:
            for line in f:
                if "can_rcv" in line or "can_send" in line:
                    can_present = True
                    break
except Exception:
    pass
if can_present:
    print(PASS)
    results.append((10, "CAN proc/kallsyms", PASS))
else:
    print(SKIP)
    results.append((10, "CAN proc/kallsyms", SKIP))

# ── Summary ──────────────────────────────────────────────────────────────────
print()
passed = sum(1 for _, _, s in results if s == PASS)
failed = sum(1 for _, _, s in results if s == FAIL)
skipped = sum(1 for _, _, s in results if s == SKIP)
total = len(results)
print(f"Results: {passed}/{total} passed, {failed} failed, {skipped} skipped")
if failed > 0:
    sys.exit(1)
