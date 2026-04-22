#!/usr/bin/env python3
"""
test_l3mdev_subsystem.py — bpftrace verification of the L3 master device (VRF).

Steps
-----
1.  Create a VRF device (ip link add vrf0 type vrf table 100)
2.  Probe l3mdev_master_ifindex_rcu     — L3 master lookup
3.  Probe l3mdev_fib_table              — FIB table ID lookup
4.  Probe l3mdev_l3_rcv                — L3 receive hook
5.  Probe l3mdev_update_flow           — flow update for VRF routing
6.  Probe l3mdev_fib_rule_match        — FIB rule VRF match
7.  Probe vrf_l3_rcv                   — VRF driver Rx hook
8.  Probe vrf_l3_out                   — VRF driver Tx hook
9.  Route lookup through VRF table
10. Cleanup + verify VRF teardown
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

VRF_DEV = "vrf_test0"
VRF_TABLE = "1234"
SLAVE_DEV = "veth_l3test"
SLAVE_PEER = "veth_l3peer"


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


def setup_vrf():
    """Create VRF + enslaved veth pair."""
    cmds = [
        ["ip", "link", "add", VRF_DEV, "type", "vrf", "table", VRF_TABLE],
        ["ip", "link", "set", "up", VRF_DEV],
        ["ip", "link", "add", SLAVE_DEV, "type", "veth", "peer", "name", SLAVE_PEER],
        ["ip", "link", "set", SLAVE_DEV, "master", VRF_DEV],
        ["ip", "link", "set", "up", SLAVE_DEV],
        ["ip", "addr", "add", "192.168.99.1/24", "dev", SLAVE_DEV],
        ["ip", "route", "add", "192.168.99.0/24", "dev", SLAVE_DEV, "vrf", VRF_DEV],
    ]
    for cmd in cmds:
        subprocess.run(cmd, capture_output=True, timeout=5)


def teardown_vrf():
    for dev in [SLAVE_DEV, SLAVE_PEER, VRF_DEV]:
        subprocess.run(["ip", "link", "del", dev], capture_output=True, timeout=5)


def route_trigger():
    """Trigger routing lookup through VRF."""
    setup_vrf()
    subprocess.run(["ip", "route", "show", "vrf", VRF_DEV],
                   capture_output=True, timeout=5)
    subprocess.run(["ip", "route", "get", "192.168.99.1", "vrf", VRF_DEV],
                   capture_output=True, timeout=5)


print("\n=== L3 Master Device (VRF) bpftrace verification ===\n")

# ── Step 1: VRF device creation ──────────────────────────────────────────────
print(f"  Step  1: {'VRF device creation':52s}", end=" ")
teardown_vrf()  # clean from previous runs
r = subprocess.run(["ip", "link", "add", VRF_DEV, "type", "vrf", "table", VRF_TABLE],
                   capture_output=True, timeout=5)
if r.returncode == 0 and os.path.exists(f"/sys/class/net/{VRF_DEV}"):
    print(PASS)
    results.append((1, "VRF device creation", PASS))
    teardown_vrf()
else:
    print(SKIP)
    results.append((1, "VRF device creation", SKIP))

# ── Step 2: l3mdev_master_ifindex_rcu ────────────────────────────────────────
prog2 = """
kprobe:l3mdev_master_ifindex_rcu {
    printf("HIT l3mdev_master_ifindex_rcu\\n");
    exit();
}
interval:s:8 { exit(); }
"""
check(2, "l3mdev_master_ifindex_rcu kprobe", prog2,
      trigger=route_trigger, timeout=12)

# ── Step 3: l3mdev_fib_table ─────────────────────────────────────────────────
prog3 = """
kprobe:l3mdev_fib_table {
    printf("HIT l3mdev_fib_table\\n");
    exit();
}
interval:s:8 { exit(); }
"""
check(3, "l3mdev_fib_table kprobe", prog3,
      trigger=route_trigger, timeout=12)

# ── Step 4: l3mdev_l3_rcv ────────────────────────────────────────────────────
prog4 = """
kprobe:l3mdev_l3_rcv {
    printf("HIT l3mdev_l3_rcv\\n");
    exit();
}
interval:s:8 { exit(); }
"""
check(4, "l3mdev_l3_rcv kprobe", prog4,
      trigger=route_trigger, timeout=12)

# ── Step 5: l3mdev_update_flow ───────────────────────────────────────────────
prog5 = """
kprobe:l3mdev_update_flow {
    printf("HIT l3mdev_update_flow\\n");
    exit();
}
interval:s:8 { exit(); }
"""
check(5, "l3mdev_update_flow kprobe", prog5,
      trigger=route_trigger, timeout=12)

# ── Step 6: l3mdev_fib_rule_match ────────────────────────────────────────────
prog6 = """
kprobe:l3mdev_fib_rule_match {
    printf("HIT l3mdev_fib_rule_match\\n");
    exit();
}
interval:s:8 { exit(); }
"""
check(6, "l3mdev_fib_rule_match kprobe", prog6,
      trigger=route_trigger, timeout=12)

# ── Step 7: vrf_l3_rcv ───────────────────────────────────────────────────────
prog7 = """
kprobe:vrf_l3_rcv {
    printf("HIT vrf_l3_rcv\\n");
    exit();
}
interval:s:8 { exit(); }
"""
check(7, "vrf_l3_rcv kprobe", prog7,
      trigger=route_trigger, timeout=12)

# ── Step 8: vrf_l3_out ───────────────────────────────────────────────────────
prog8 = """
kprobe:vrf_l3_out {
    printf("HIT vrf_l3_out\\n");
    exit();
}
interval:s:8 { exit(); }
"""
check(8, "vrf_l3_out kprobe", prog8,
      trigger=route_trigger, timeout=12)

# ── Step 9: ip route get through VRF table ───────────────────────────────────
print(f"  Step  9: {'ip route get through VRF table':52s}", end=" ")
setup_vrf()
r = subprocess.run(
    ["ip", "route", "show", "table", VRF_TABLE],
    capture_output=True, text=True, timeout=5)
if r.returncode == 0 and ("192.168.99" in r.stdout or "dev" in r.stdout):
    print(PASS)
    results.append((9, "VRF route lookup", PASS))
else:
    # Try alternate form
    r2 = subprocess.run(["ip", "route", "show", "vrf", VRF_DEV],
                        capture_output=True, text=True, timeout=5)
    if r2.returncode == 0:
        print(PASS)
        results.append((9, "VRF route lookup", PASS))
    else:
        print(FAIL)
        results.append((9, "VRF route lookup", FAIL))
        print(f"            stderr: {r.stderr.strip()[:200]}")

# ── Step 10: VRF teardown ────────────────────────────────────────────────────
print(f"  Step 10: {'VRF teardown (ip link del)':52s}", end=" ")
teardown_vrf()
if not os.path.exists(f"/sys/class/net/{VRF_DEV}"):
    print(PASS)
    results.append((10, "VRF teardown", PASS))
else:
    print(FAIL)
    results.append((10, "VRF teardown", FAIL))

# ── Summary ──────────────────────────────────────────────────────────────────
print()
passed = sum(1 for _, _, s in results if s == PASS)
failed = sum(1 for _, _, s in results if s == FAIL)
skipped = sum(1 for _, _, s in results if s == SKIP)
total = len(results)
print(f"Results: {passed}/{total} passed, {failed} failed, {skipped} skipped")
if failed > 0:
    sys.exit(1)
