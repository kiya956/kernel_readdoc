#!/usr/bin/env python3
"""
test_bluetooth_subsystem.py — bpftrace-based verification of the Bluetooth subsystem.

Steps
-----
1.  Probe hci_send_frame                — HCI frame transmission to controller
2.  Probe hci_recv_frame                — HCI frame reception from controller
3.  Probe l2cap_recv_acldata            — ACL data reassembly into L2CAP
4.  Probe hci_event_packet              — HCI event processing
5.  Probe sco_connect                   — SCO audio link establishment
6.  Probe hci_register_dev              — HCI controller registration
7.  Probe l2cap_connect                 — L2CAP channel connection initiation
8.  Probe bt_sock_create                — AF_BLUETOOTH socket creation
9.  Probe mgmt_control                  — management command processing
10. Check /sys/class/bluetooth           — sysfs presence of BT controllers
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


print("\n=== Bluetooth subsystem bpftrace verification ===\n")

# Helper: attempt to trigger Bluetooth activity
def trigger_bt_socket():
    """Try creating an AF_BLUETOOTH socket to trigger bt_sock_create."""
    import socket
    AF_BLUETOOTH = 31
    try:
        s = socket.socket(AF_BLUETOOTH, socket.SOCK_RAW, 1)  # BTPROTO_HCI
        s.close()
    except (OSError, PermissionError):
        pass


def trigger_hci_tool():
    """Run hciconfig or bluetoothctl to trigger HCI activity."""
    subprocess.run(["hciconfig", "-a"], capture_output=True, timeout=5)


# ── Step 1: hci_send_frame ──────────────────────────────────────────────────
prog1 = """
kprobe:hci_send_frame {
    printf("HIT hci_send_frame\\n");
    exit();
}
interval:s:8 { exit(); }
"""
check(1, "hci_send_frame HCI tx", prog1,
      trigger=trigger_hci_tool, timeout=12)

# ── Step 2: hci_recv_frame ──────────────────────────────────────────────────
prog2 = """
kprobe:hci_recv_frame {
    printf("HIT hci_recv_frame\\n");
    exit();
}
interval:s:8 { exit(); }
"""
check(2, "hci_recv_frame HCI rx", prog2,
      trigger=trigger_hci_tool, timeout=12)

# ── Step 3: l2cap_recv_acldata ──────────────────────────────────────────────
prog3 = """
kprobe:l2cap_recv_acldata {
    printf("HIT l2cap_recv_acldata\\n");
    exit();
}
interval:s:8 { exit(); }
"""
check(3, "l2cap_recv_acldata ACL reassembly", prog3,
      trigger=trigger_hci_tool, timeout=12)

# ── Step 4: hci_event_packet ────────────────────────────────────────────────
prog4 = """
kprobe:hci_event_packet {
    printf("HIT hci_event_packet\\n");
    exit();
}
interval:s:8 { exit(); }
"""
check(4, "hci_event_packet HCI event processing", prog4,
      trigger=trigger_hci_tool, timeout=12)

# ── Step 5: sco_connect ─────────────────────────────────────────────────────
prog5 = """
kprobe:sco_connect {
    printf("HIT sco_connect\\n");
    exit();
}
interval:s:8 { exit(); }
"""
check(5, "sco_connect SCO audio link", prog5, timeout=12)

# ── Step 6: hci_register_dev ────────────────────────────────────────────────
prog6 = """
kprobe:hci_register_dev {
    printf("HIT hci_register_dev\\n");
    exit();
}
interval:s:8 { exit(); }
"""
check(6, "hci_register_dev controller registration", prog6, timeout=12)

# ── Step 7: l2cap_connect ───────────────────────────────────────────────────
prog7 = """
kprobe:l2cap_connect {
    printf("HIT l2cap_connect\\n");
    exit();
}
interval:s:8 { exit(); }
"""
check(7, "l2cap_connect channel connection", prog7, timeout=12)

# ── Step 8: bt_sock_create ──────────────────────────────────────────────────
prog8 = """
kprobe:bt_sock_create {
    printf("HIT bt_sock_create\\n");
    exit();
}
interval:s:8 { exit(); }
"""
check(8, "bt_sock_create AF_BLUETOOTH socket", prog8,
      trigger=trigger_bt_socket, timeout=12)

# ── Step 9: mgmt_control ───────────────────────────────────────────────────
prog9 = """
kprobe:mgmt_control {
    printf("HIT mgmt_control\\n");
    exit();
}
interval:s:8 { exit(); }
"""
def trigger_mgmt():
    subprocess.run(["btmgmt", "info"], capture_output=True, timeout=5)
check(9, "mgmt_control management command", prog9,
      trigger=trigger_mgmt, timeout=12)

# ── Step 10: /sys/class/bluetooth sysfs check ──────────────────────────────
print(f"  Step 10: {'sysfs /sys/class/bluetooth presence':50s}", end=" ")
try:
    bt_path = "/sys/class/bluetooth"
    if os.path.isdir(bt_path):
        controllers = os.listdir(bt_path)
        if controllers:
            print(PASS)
            results.append((10, "sysfs bluetooth presence", PASS))
            print(f"            controllers: {', '.join(controllers)}")
        else:
            print(SKIP)
            results.append((10, "sysfs bluetooth presence", SKIP))
            print("            (no Bluetooth controllers found)")
    else:
        print(SKIP)
        results.append((10, "sysfs bluetooth presence", SKIP))
        print("            (/sys/class/bluetooth not present)")
except Exception as e:
    print(FAIL)
    results.append((10, "sysfs bluetooth presence", FAIL))
    print(f"            error: {e}")

# ── Summary ──────────────────────────────────────────────────────────────────
print()
passed = sum(1 for _, _, s in results if s == PASS)
failed = sum(1 for _, _, s in results if s == FAIL)
skipped = sum(1 for _, _, s in results if s == SKIP)
total = len(results)
print(f"Results: {passed}/{total} passed, {failed} failed, {skipped} skipped")
if failed > 0:
    sys.exit(1)
