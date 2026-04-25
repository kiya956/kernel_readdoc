#!/usr/bin/env python3
"""
test_802_subsystem.py — bpftrace-based verification of the IEEE 802 LLC/SNAP subsystem.

Steps
-----
1.  Probe p8022_rcv                    — 802.2 LLC frame receive
2.  Probe register_8022_client         — LLC SAP client registration
3.  Probe unregister_8022_client       — LLC SAP client removal
4.  Probe snap_rcv                     — SNAP frame receive
5.  Probe register_snap_client         — SNAP protocol registration
6.  Probe unregister_snap_client       — SNAP protocol removal
7.  Probe stp_proto_register           — STP protocol registration
8.  Probe stp_proto_unregister         — STP protocol unregistration
9.  Probe fc_type_trans                — Fibre Channel type translation
10. Check 802 module/symbols loaded    — /proc/kallsyms round-trip
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
          expect: str = "HIT", timeout: int = 12):
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


print("\n=== IEEE 802 LLC/SNAP subsystem bpftrace verification ===\n")

# ── Step 1: Probe p8022_rcv — 802.2 LLC frame receive ──
check(1, "p8022_rcv (LLC frame receive)", r"""
kprobe:p8022_rcv {
    printf("HIT p8022_rcv\n");
    exit();
}
interval:s:5 { printf("TIMEOUT\n"); exit(); }
""", timeout=12)

# ── Step 2: Probe register_8022_client — LLC SAP client registration ──
def trigger_load_8021q():
    """Try loading 8021q or bridge to trigger LLC registrations."""
    subprocess.run(["modprobe", "8021q"], capture_output=True, timeout=5)

check(2, "register_8022_client (SAP registration)", r"""
kprobe:register_8022_client {
    printf("HIT register_8022_client\n");
    exit();
}
interval:s:5 { printf("TIMEOUT\n"); exit(); }
""", trigger=trigger_load_8021q, timeout=12)

# ── Step 3: Probe unregister_8022_client — LLC SAP client removal ──
check(3, "unregister_8022_client (SAP removal)", r"""
kprobe:unregister_8022_client {
    printf("HIT unregister_8022_client\n");
    exit();
}
interval:s:5 { printf("TIMEOUT\n"); exit(); }
""", timeout=12)

# ── Step 4: Probe snap_rcv — SNAP frame receive ──
check(4, "snap_rcv (SNAP frame receive)", r"""
kprobe:snap_rcv {
    printf("HIT snap_rcv\n");
    exit();
}
interval:s:5 { printf("TIMEOUT\n"); exit(); }
""", timeout=12)

# ── Step 5: Probe register_snap_client — SNAP protocol registration ──
check(5, "register_snap_client (SNAP registration)", r"""
kprobe:register_snap_client {
    printf("HIT register_snap_client\n");
    exit();
}
interval:s:5 { printf("TIMEOUT\n"); exit(); }
""", trigger=trigger_load_8021q, timeout=12)

# ── Step 6: Probe unregister_snap_client — SNAP protocol removal ──
check(6, "unregister_snap_client (SNAP removal)", r"""
kprobe:unregister_snap_client {
    printf("HIT unregister_snap_client\n");
    exit();
}
interval:s:5 { printf("TIMEOUT\n"); exit(); }
""", timeout=12)

# ── Step 7: Probe stp_proto_register — STP protocol registration ──
def trigger_load_bridge():
    """Load bridge module to trigger stp_proto_register."""
    subprocess.run(["modprobe", "bridge"], capture_output=True, timeout=5)

check(7, "stp_proto_register (STP registration)", r"""
kprobe:stp_proto_register {
    printf("HIT stp_proto_register\n");
    exit();
}
interval:s:5 { printf("TIMEOUT\n"); exit(); }
""", trigger=trigger_load_bridge, timeout=12)

# ── Step 8: Probe stp_proto_unregister — STP protocol unregistration ──
check(8, "stp_proto_unregister (STP unregistration)", r"""
kprobe:stp_proto_unregister {
    printf("HIT stp_proto_unregister\n");
    exit();
}
interval:s:5 { printf("TIMEOUT\n"); exit(); }
""", timeout=12)

# ── Step 9: Probe fc_type_trans — Fibre Channel type translation ──
check(9, "fc_type_trans (FC type translation)", r"""
kprobe:fc_type_trans {
    printf("HIT fc_type_trans\n");
    exit();
}
interval:s:5 { printf("TIMEOUT\n"); exit(); }
""", timeout=12)

# ── Step 10: Check 802 module/symbols loaded — /proc/kallsyms ──
print()  # visual separator before non-bpftrace step
step_num = 10
name = "/proc/kallsyms 802 symbol check"
try:
    with open("/proc/kallsyms", "r") as f:
        kallsyms = f.read()
    targets = ["p8022_rcv", "snap_rcv", "stp_proto_register"]
    found = [sym for sym in targets if sym in kallsyms]
    if found:
        status = PASS
        detail = ", ".join(found)
        results.append((step_num, name, status))
        print(f"  Step {step_num:2d}: {name:50s} {status}")
        print(f"            found: {detail}")
    else:
        status = FAIL
        results.append((step_num, name, status))
        print(f"  Step {step_num:2d}: {name:50s} {status}")
        print("            none of the expected 802 symbols found in kallsyms")
except PermissionError:
    results.append((step_num, name, SKIP))
    print(f"  Step {step_num:2d}: {name:50s} {SKIP}")
    print("            cannot read /proc/kallsyms (permission denied)")
except Exception as e:
    results.append((step_num, name, FAIL))
    print(f"  Step {step_num:2d}: {name:50s} {FAIL}")
    print(f"            error: {e}")

# ── Summary ──
print("\n" + "=" * 62)
passed = sum(1 for _, _, s in results if s == PASS)
failed = sum(1 for _, _, s in results if s == FAIL)
skipped = sum(1 for _, _, s in results if s == SKIP)
total = len(results)
print(f"  Total: {total}   Passed: {passed}   Failed: {failed}   Skipped: {skipped}")
print("=" * 62 + "\n")

sys.exit(1 if failed else 0)
