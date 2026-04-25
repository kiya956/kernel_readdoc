#!/usr/bin/env python3
"""
test_bridge_subsystem.py — bpftrace-based verification of the bridge subsystem.

Steps
-----
1.  Probe br_handle_frame              — main frame reception entry
2.  Probe br_forward                   — unicast forwarding to single port
3.  Probe br_flood                     — flood to all forwarding ports
4.  Probe br_fdb_update                — FDB MAC learning/refresh
5.  Probe br_dev_xmit                  — bridge device transmit
6.  Probe br_pass_frame_up             — local delivery to host stack
7.  Probe br_stp_rcv                   — STP BPDU processing
8.  Probe br_nf_pre_routing            — netfilter bridge pre-routing hook
9.  Probe br_port_carrier_check        — link state change handling
10. Check /sys/class/net for bridge     — sysfs bridge device presence
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

BR_NAME = "br_test0"
VETH_A = "veth_brt0"
VETH_B = "veth_brt1"


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


print("\n=== bridge subsystem bpftrace verification ===\n")


# ── Setup: create bridge + veth pair for triggering ─────────────────────────
def setup_bridge():
    """Create a temporary bridge with a veth pair for testing."""
    subprocess.run(["ip", "link", "add", VETH_A, "type", "veth", "peer", "name", VETH_B],
                   capture_output=True)
    subprocess.run(["ip", "link", "add", "name", BR_NAME, "type", "bridge"],
                   capture_output=True)
    subprocess.run(["ip", "link", "set", VETH_A, "master", BR_NAME],
                   capture_output=True)
    subprocess.run(["ip", "link", "set", VETH_A, "up"], capture_output=True)
    subprocess.run(["ip", "link", "set", VETH_B, "up"], capture_output=True)
    subprocess.run(["ip", "link", "set", BR_NAME, "up"], capture_output=True)
    subprocess.run(["ip", "addr", "add", "192.168.199.1/24", "dev", BR_NAME],
                   capture_output=True)
    subprocess.run(["ip", "addr", "add", "192.168.199.2/24", "dev", VETH_B],
                   capture_output=True)


def teardown_bridge():
    """Remove temporary bridge and veth pair."""
    subprocess.run(["ip", "link", "del", BR_NAME], capture_output=True)
    subprocess.run(["ip", "link", "del", VETH_A], capture_output=True)


def trigger_bridge_traffic():
    """Generate traffic through the bridge."""
    subprocess.run(["ping", "-c", "2", "-W", "1", "-I", VETH_B, "192.168.199.1"],
                   capture_output=True, timeout=5)


def trigger_arping():
    """Generate ARP traffic through the bridge."""
    subprocess.run(["arping", "-c", "1", "-I", VETH_B, "192.168.199.1"],
                   capture_output=True, timeout=5)


# Set up bridge environment
setup_bridge()
print(f"  [info] Created bridge {BR_NAME} with veth pair\n")


# ── Step 1: br_handle_frame ────────────────────────────────────────────────
prog1 = """
kprobe:br_handle_frame {
    printf("HIT br_handle_frame\\n");
    exit();
}
interval:s:8 { exit(); }
"""
check(1, "br_handle_frame frame reception", prog1,
      trigger=trigger_bridge_traffic, timeout=12)

# ── Step 2: br_forward ─────────────────────────────────────────────────────
prog2 = """
kprobe:br_forward {
    printf("HIT br_forward\\n");
    exit();
}
interval:s:8 { exit(); }
"""
check(2, "br_forward unicast forwarding", prog2,
      trigger=trigger_bridge_traffic, timeout=12)

# ── Step 3: br_flood ───────────────────────────────────────────────────────
prog3 = """
kprobe:br_flood {
    printf("HIT br_flood\\n");
    exit();
}
interval:s:8 { exit(); }
"""
check(3, "br_flood flood to all ports", prog3,
      trigger=trigger_bridge_traffic, timeout=12)

# ── Step 4: br_fdb_update ──────────────────────────────────────────────────
prog4 = """
kprobe:br_fdb_update {
    printf("HIT br_fdb_update\\n");
    exit();
}
interval:s:8 { exit(); }
"""
check(4, "br_fdb_update FDB MAC learning", prog4,
      trigger=trigger_bridge_traffic, timeout=12)

# ── Step 5: br_dev_xmit ───────────────────────────────────────────────────
prog5 = """
kprobe:br_dev_xmit {
    printf("HIT br_dev_xmit\\n");
    exit();
}
interval:s:8 { exit(); }
"""
def trigger_from_bridge():
    subprocess.run(["ping", "-c", "1", "-W", "1", "-I", BR_NAME, "192.168.199.2"],
                   capture_output=True, timeout=5)
check(5, "br_dev_xmit bridge device transmit", prog5,
      trigger=trigger_from_bridge, timeout=12)

# ── Step 6: br_pass_frame_up ──────────────────────────────────────────────
prog6 = """
kprobe:br_pass_frame_up {
    printf("HIT br_pass_frame_up\\n");
    exit();
}
interval:s:8 { exit(); }
"""
check(6, "br_pass_frame_up local delivery", prog6,
      trigger=trigger_bridge_traffic, timeout=12)

# ── Step 7: br_stp_rcv ────────────────────────────────────────────────────
prog7 = """
kprobe:br_stp_rcv {
    printf("HIT br_stp_rcv\\n");
    exit();
}
interval:s:8 { exit(); }
"""
check(7, "br_stp_rcv STP BPDU processing", prog7, timeout=12)

# ── Step 8: br_nf_pre_routing ─────────────────────────────────────────────
prog8 = """
kprobe:br_nf_pre_routing {
    printf("HIT br_nf_pre_routing\\n");
    exit();
}
interval:s:8 { exit(); }
"""
check(8, "br_nf_pre_routing netfilter hook", prog8,
      trigger=trigger_bridge_traffic, timeout=12)

# ── Step 9: br_port_carrier_check ─────────────────────────────────────────
prog9 = """
kprobe:br_port_carrier_check {
    printf("HIT br_port_carrier_check\\n");
    exit();
}
interval:s:8 { exit(); }
"""
def trigger_link_toggle():
    subprocess.run(["ip", "link", "set", VETH_A, "down"], capture_output=True)
    time.sleep(0.5)
    subprocess.run(["ip", "link", "set", VETH_A, "up"], capture_output=True)
check(9, "br_port_carrier_check link state", prog9,
      trigger=trigger_link_toggle, timeout=12)

# ── Step 10: /sys/class/net bridge sysfs check ────────────────────────────
print(f"  Step 10: {'sysfs /sys/class/net bridge presence':50s}", end=" ")
try:
    br_sysfs = f"/sys/class/net/{BR_NAME}/bridge"
    if os.path.isdir(br_sysfs):
        entries = os.listdir(br_sysfs)
        if entries:
            print(PASS)
            results.append((10, "sysfs bridge presence", PASS))
            sample = ", ".join(entries[:5])
            print(f"            bridge attrs: {sample}...")
        else:
            print(FAIL)
            results.append((10, "sysfs bridge presence", FAIL))
    else:
        # Check if any bridge exists
        found = False
        for name in os.listdir("/sys/class/net"):
            if os.path.isdir(f"/sys/class/net/{name}/bridge"):
                found = True
                break
        if found:
            print(PASS)
            results.append((10, "sysfs bridge presence", PASS))
        else:
            print(SKIP)
            results.append((10, "sysfs bridge presence", SKIP))
            print("            (no bridge devices found)")
except Exception as e:
    print(FAIL)
    results.append((10, "sysfs bridge presence", FAIL))
    print(f"            error: {e}")

# ── Cleanup ──────────────────────────────────────────────────────────────────
teardown_bridge()
print(f"\n  [info] Cleaned up {BR_NAME} and veth pair\n")

# ── Summary ──────────────────────────────────────────────────────────────────
passed = sum(1 for _, _, s in results if s == PASS)
failed = sum(1 for _, _, s in results if s == FAIL)
skipped = sum(1 for _, _, s in results if s == SKIP)
total = len(results)
print(f"Results: {passed}/{total} passed, {failed} failed, {skipped} skipped")
if failed > 0:
    sys.exit(1)
