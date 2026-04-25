#!/usr/bin/env python3
"""
test_8021q_subsystem.py — bpftrace-based verification of the 802.1Q VLAN subsystem.

Steps
-----
1.  Probe register_vlan_dev            — VLAN device registration
2.  Probe vlan_dev_hard_start_xmit     — VLAN TX path (tag insert)
3.  Probe vlan_skb_recv                — VLAN RX path (tag strip)
4.  Probe vlan_ioctl_handler           — legacy vconfig ioctl
5.  Probe vlan_newlink                 — netlink VLAN creation
6.  Probe vlan_dev_change_flags        — VLAN device flag change
7.  Probe vlan_vid_add                 — VLAN ID add to device
8.  Probe vlan_vid_del                 — VLAN ID remove from device
9.  Probe vlan_dev_open                — VLAN device open
10. Create/delete VLAN and verify      — userspace round-trip
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ensure_8021q():
    """Make sure the 8021q module is loaded."""
    subprocess.run(["modprobe", "8021q"], capture_output=True)


def _create_vlan():
    """Create a test VLAN interface on loopback."""
    _ensure_8021q()
    subprocess.run(
        ["ip", "link", "add", "link", "lo", "name", "lo.100",
         "type", "vlan", "id", "100"],
        capture_output=True,
    )


def _delete_vlan():
    """Delete the test VLAN interface."""
    subprocess.run(
        ["ip", "link", "del", "lo.100"],
        capture_output=True,
    )


def _create_and_delete_vlan():
    """Create then delete a VLAN — exercises register + unregister paths."""
    _create_vlan()
    time.sleep(0.3)
    _delete_vlan()


def _bring_up_and_down_vlan():
    """Create, bring up, then tear down a VLAN interface."""
    _create_vlan()
    time.sleep(0.2)
    subprocess.run(["ip", "link", "set", "lo.100", "up"], capture_output=True)
    time.sleep(0.3)
    subprocess.run(["ip", "link", "set", "lo.100", "down"], capture_output=True)
    time.sleep(0.1)
    _delete_vlan()


def _send_traffic_on_vlan():
    """Create VLAN, bring it up, send a packet, then tear down."""
    _create_vlan()
    subprocess.run(["ip", "link", "set", "lo.100", "up"], capture_output=True)
    subprocess.run(
        ["ip", "addr", "add", "192.0.2.1/32", "dev", "lo.100"],
        capture_output=True,
    )
    time.sleep(0.2)
    subprocess.run(
        ["ping", "-c", "1", "-W", "1", "-I", "lo.100", "192.0.2.1"],
        capture_output=True,
    )
    time.sleep(0.2)
    _delete_vlan()


print("\n=== 802.1Q VLAN subsystem bpftrace verification ===\n")

_ensure_8021q()
# Clean up any leftover test interface
_delete_vlan()

# ---------------------------------------------------------------------------
# Step 1 — register_vlan_dev (VLAN device registration)
# ---------------------------------------------------------------------------
check(1, "register_vlan_dev (VLAN registration)", r"""
kprobe:register_vlan_dev {
    printf("HIT register_vlan_dev\n");
    exit();
}
""", trigger=_create_and_delete_vlan)

# ---------------------------------------------------------------------------
# Step 2 — vlan_dev_hard_start_xmit (TX path)
# ---------------------------------------------------------------------------
check(2, "vlan_dev_hard_start_xmit (TX path)", r"""
kprobe:vlan_dev_hard_start_xmit {
    printf("HIT vlan_dev_hard_start_xmit\n");
    exit();
}
""", trigger=_send_traffic_on_vlan)

# ---------------------------------------------------------------------------
# Step 3 — vlan_skb_recv (RX path / tag strip)
# ---------------------------------------------------------------------------
check(3, "vlan_skb_recv / vlan_do_receive (RX path)", r"""
kprobe:vlan_do_receive {
    printf("HIT vlan_do_receive\n");
    exit();
}

kprobe:vlan_skb_recv {
    printf("HIT vlan_skb_recv\n");
    exit();
}
""", trigger=_send_traffic_on_vlan, expect="HIT")

# ---------------------------------------------------------------------------
# Step 4 — vlan_ioctl_handler (legacy ioctl)
# ---------------------------------------------------------------------------
check(4, "vlan_ioctl_handler (legacy ioctl)", r"""
kprobe:vlan_ioctl_handler {
    printf("HIT vlan_ioctl_handler\n");
    exit();
}
""", trigger=lambda: None)
# Note: triggering a real ioctl requires vconfig which may not be installed;
# we attach and let it time out or skip.

# ---------------------------------------------------------------------------
# Step 5 — vlan_newlink (netlink VLAN creation)
# ---------------------------------------------------------------------------
check(5, "vlan_newlink (netlink VLAN creation)", r"""
kprobe:vlan_newlink {
    printf("HIT vlan_newlink\n");
    exit();
}
""", trigger=_create_and_delete_vlan)

# ---------------------------------------------------------------------------
# Step 6 — vlan_dev_change_flags (flag change)
# ---------------------------------------------------------------------------
check(6, "vlan_dev_change_flags (flag change)", r"""
kprobe:vlan_dev_change_flags {
    printf("HIT vlan_dev_change_flags\n");
    exit();
}
""", trigger=_bring_up_and_down_vlan)

# ---------------------------------------------------------------------------
# Step 7 — vlan_vid_add (VID add)
# ---------------------------------------------------------------------------
check(7, "vlan_vid_add (VID add to device)", r"""
kprobe:vlan_vid_add {
    printf("HIT vlan_vid_add\n");
    exit();
}
""", trigger=_create_and_delete_vlan)

# ---------------------------------------------------------------------------
# Step 8 — vlan_vid_del (VID remove)
# ---------------------------------------------------------------------------
check(8, "vlan_vid_del (VID remove from device)", r"""
kprobe:vlan_vid_del {
    printf("HIT vlan_vid_del\n");
    exit();
}
""", trigger=_create_and_delete_vlan)

# ---------------------------------------------------------------------------
# Step 9 — vlan_dev_open (VLAN device open)
# ---------------------------------------------------------------------------
check(9, "vlan_dev_open (VLAN device open)", r"""
kprobe:vlan_dev_open {
    printf("HIT vlan_dev_open\n");
    exit();
}
""", trigger=_bring_up_and_down_vlan)

# ---------------------------------------------------------------------------
# Step 10 — Userspace round-trip (create / verify / delete)
# ---------------------------------------------------------------------------
print()  # visual separator before the non-bpftrace step

step_num = 10
name = "Userspace VLAN round-trip"
try:
    _ensure_8021q()
    # Ensure clean slate
    _delete_vlan()

    # Create VLAN
    rc = subprocess.run(
        ["ip", "link", "add", "link", "lo", "name", "lo.100",
         "type", "vlan", "id", "100"],
        capture_output=True, text=True,
    )
    if rc.returncode != 0:
        raise RuntimeError(f"create failed: {rc.stderr.strip()}")

    # Verify it exists
    show = subprocess.run(
        ["ip", "-d", "link", "show", "lo.100"],
        capture_output=True, text=True,
    )
    if "lo.100" not in show.stdout or "vlan" not in show.stdout:
        raise RuntimeError("lo.100 not visible in ip link show")

    # Verify VLAN ID present
    if "id 100" not in show.stdout:
        raise RuntimeError("VLAN id 100 not found in ip link output")

    # Delete
    rc = subprocess.run(
        ["ip", "link", "del", "lo.100"],
        capture_output=True, text=True,
    )
    if rc.returncode != 0:
        raise RuntimeError(f"delete failed: {rc.stderr.strip()}")

    # Confirm gone
    verify = subprocess.run(
        ["ip", "link", "show", "lo.100"],
        capture_output=True, text=True,
    )
    if verify.returncode == 0:
        raise RuntimeError("lo.100 still present after deletion")

    status = PASS
except Exception as exc:
    status = FAIL
    print(f"            error: {exc}")

results.append((step_num, name, status))
print(f"  Step {step_num:2d}: {name:50s} {status}")

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print("\n" + "=" * 64)
total = len(results)
passed = sum(1 for _, _, s in results if s == PASS)
failed = sum(1 for _, _, s in results if s == FAIL)
skipped = sum(1 for _, _, s in results if s == SKIP)
print(f"  Total: {total}   Passed: {passed}   Failed: {failed}   Skipped: {skipped}")
print("=" * 64 + "\n")

sys.exit(1 if failed else 0)
