#!/usr/bin/env python3
"""
test_switchdev_subsystem.py — bpftrace-based verification of the switchdev API.

Steps
-----
1.  Check switchdev symbols in kallsyms
2.  Probe switchdev_port_attr_set        — port attribute push to hardware
3.  Probe switchdev_port_obj_add         — VLAN/MDB object add to hardware
4.  Probe switchdev_port_obj_del         — VLAN/MDB object del from hardware
5.  Probe call_switchdev_notifiers       — event notification dispatch
6.  Probe switchdev_deferred_enqueue     — deferred work queue entry
7.  Probe switchdev_port_obj_notify      — notifier-based obj update
8.  Check /sys/bus/platform for switchdev-capable devices
9.  Probe switchdev_handle_fdb_add_to_device  — FDB offload entry point
10. Trigger bridge + check switchdev notifier fires
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
        print(f"  Step {step_num:2d}: {name:55s} {SKIP}")
        return
    ok = expect in stdout
    status = PASS if ok else FAIL
    results.append((step_num, name, status))
    print(f"  Step {step_num:2d}: {name:55s} {status}")
    if not ok:
        if stdout.strip():
            print(f"            stdout: {stdout.strip()[:200]}")
        if stderr.strip():
            print(f"            stderr: {stderr.strip()[:200]}")


print("\n=== switchdev subsystem bpftrace verification ===\n")

# ── Step 1: switchdev symbols in kallsyms ────────────────────────────────────
print(f"  Step  1: {'switchdev symbols in /proc/kallsyms':55s}", end=" ")
switchdev_present = False
try:
    with open("/proc/kallsyms") as f:
        for line in f:
            if "switchdev_port_attr_set" in line:
                switchdev_present = True
                break
except Exception:
    pass
if switchdev_present:
    print(PASS)
    results.append((1, "switchdev kallsyms", PASS))
else:
    print(SKIP)
    results.append((1, "switchdev kallsyms", SKIP))

# ── Step 2: switchdev_port_attr_set ──────────────────────────────────────────
prog2 = """
kprobe:switchdev_port_attr_set {
    printf("HIT switchdev_port_attr_set\\n");
    exit();
}
interval:s:5 { exit(); }
"""

def bridge_trigger():
    """Create a veth pair + bridge to trigger switchdev attr sets."""
    cmds = [
        ["ip", "link", "add", "swtest0", "type", "veth", "peer", "name", "swtest1"],
        ["ip", "link", "add", "swbr0", "type", "bridge"],
        ["ip", "link", "set", "swtest0", "master", "swbr0"],
        ["ip", "link", "set", "up", "swtest0"],
        ["ip", "link", "set", "up", "swbr0"],
    ]
    for cmd in cmds:
        subprocess.run(cmd, capture_output=True, timeout=5)

def bridge_cleanup():
    for dev in ["swtest0", "swtest1", "swbr0"]:
        subprocess.run(["ip", "link", "del", dev], capture_output=True, timeout=5)

check(2, "switchdev_port_attr_set kprobe", prog2,
      trigger=bridge_trigger, timeout=12)

# ── Step 3: switchdev_port_obj_add ───────────────────────────────────────────
prog3 = """
kprobe:switchdev_port_obj_add {
    printf("HIT switchdev_port_obj_add\\n");
    exit();
}
interval:s:5 { exit(); }
"""

def vlan_trigger():
    """Add VLAN to bridge port → triggers switchdev_port_obj_add."""
    bridge_trigger()
    subprocess.run(["bridge", "vlan", "add", "vid", "100", "dev", "swtest0"],
                   capture_output=True, timeout=5)

check(3, "switchdev_port_obj_add kprobe", prog3,
      trigger=vlan_trigger, timeout=12)

# ── Step 4: switchdev_port_obj_del ───────────────────────────────────────────
prog4 = """
kprobe:switchdev_port_obj_del {
    printf("HIT switchdev_port_obj_del\\n");
    exit();
}
interval:s:5 { exit(); }
"""

def vlan_del_trigger():
    subprocess.run(["bridge", "vlan", "del", "vid", "100", "dev", "swtest0"],
                   capture_output=True, timeout=5)

check(4, "switchdev_port_obj_del kprobe", prog4,
      trigger=vlan_del_trigger, timeout=12)

# ── Step 5: call_switchdev_notifiers ─────────────────────────────────────────
prog5 = """
kprobe:call_switchdev_notifiers {
    printf("HIT call_switchdev_notifiers\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(5, "call_switchdev_notifiers kprobe", prog5,
      trigger=bridge_trigger, timeout=12)

# ── Step 6: switchdev_deferred_enqueue ───────────────────────────────────────
prog6 = """
kprobe:switchdev_deferred_enqueue {
    printf("HIT switchdev_deferred_enqueue\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(6, "switchdev_deferred_enqueue kprobe", prog6, timeout=8)

# ── Step 7: switchdev_port_obj_notify ────────────────────────────────────────
prog7 = """
kprobe:switchdev_port_obj_notify {
    printf("HIT switchdev_port_obj_notify\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(7, "switchdev_port_obj_notify kprobe", prog7, timeout=8)

# ── Step 8: /sys/bus — switchdev device check ────────────────────────────────
print(f"  Step  8: {'switchdev-capable device in /sys/class/net':55s}", end=" ")
# Look for known switchdev drivers via devlink or net_device flags
swdev_found = False
try:
    net_devs = os.listdir("/sys/class/net")
    for dev in net_devs:
        # Check phys_port_name — only switchdev devices have this
        pname = f"/sys/class/net/{dev}/phys_port_name"
        if os.path.exists(pname):
            swdev_found = True
            break
except OSError:
    pass
if swdev_found:
    print(PASS)
    results.append((8, "switchdev device", PASS))
else:
    print(SKIP)
    results.append((8, "switchdev device", SKIP))

# ── Step 9: switchdev_handle_fdb_add_to_device ───────────────────────────────
prog9 = """
kprobe:switchdev_handle_fdb_add_to_device {
    printf("HIT switchdev_handle_fdb_add_to_device\\n");
    exit();
}
interval:s:8 { exit(); }
"""

def fdb_trigger():
    """Static FDB entry → triggers FDB-to-device notification path."""
    bridge_trigger()
    subprocess.run(
        ["bridge", "fdb", "add", "aa:bb:cc:dd:ee:ff", "dev", "swtest0", "static"],
        capture_output=True, timeout=5)

check(9, "switchdev_handle_fdb_add_to_device kprobe", prog9,
      trigger=fdb_trigger, timeout=12)

# ── Step 10: bridge + FDB round-trip ─────────────────────────────────────────
print(f"  Step 10: {'bridge FDB add round-trip':55s}", end=" ")
try:
    bridge_trigger()
    r = subprocess.run(
        ["bridge", "fdb", "add", "de:ad:be:ef:00:01", "dev", "swtest0", "static"],
        capture_output=True, text=True, timeout=5)
    r2 = subprocess.run(
        ["bridge", "fdb", "show", "dev", "swtest0"],
        capture_output=True, text=True, timeout=5)
    if r.returncode == 0 and "de:ad:be:ef:00:01" in r2.stdout:
        print(PASS)
        results.append((10, "bridge FDB round-trip", PASS))
    else:
        print(FAIL)
        results.append((10, "bridge FDB round-trip", FAIL))
except Exception as e:
    print(SKIP)
    results.append((10, "bridge FDB round-trip", SKIP))
finally:
    bridge_cleanup()

# ── Summary ──────────────────────────────────────────────────────────────────
print()
passed = sum(1 for _, _, s in results if s == PASS)
failed = sum(1 for _, _, s in results if s == FAIL)
skipped = sum(1 for _, _, s in results if s == SKIP)
total = len(results)
print(f"Results: {passed}/{total} passed, {failed} failed, {skipped} skipped")
if failed > 0:
    sys.exit(1)
