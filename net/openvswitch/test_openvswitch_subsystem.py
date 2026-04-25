#!/usr/bin/env python3
"""
test_openvswitch_subsystem.py — bpftrace verification of the OvS kernel datapath.

Open vSwitch kernel datapath implements flow-based packet switching with
flow table lookup, action execution, and upcall to userspace ovs-vswitchd.

Steps
-----
1.  Probe ovs_dp_process_packet  — main datapath packet processing
2.  Probe ovs_flow_tbl_lookup    — flow table lookup
3.  Probe ovs_execute_actions    — execute flow actions on packet
4.  Probe ovs_dp_upcall          — upcall to userspace on flow miss
5.  Probe ovs_vport_receive      — vport packet receive entry
6.  Probe ovs_vport_add          — create new virtual port
7.  Probe ovs_dp_cmd_new         — netlink: create new datapath
8.  Probe ovs_flow_cmd_new       — netlink: install new flow
9.  Probe ovs_packet_cmd_execute — netlink: execute actions on packet
10. Check /sys/module/openvswitch — OvS module loaded
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


print("\n=== Open vSwitch Kernel Datapath bpftrace verification ===\n")

# ── Step 1: ovs_dp_process_packet ────────────────────────────────────────────
prog1 = """
kprobe:ovs_dp_process_packet {
    printf("HIT ovs_dp_process_packet\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(1, "ovs_dp_process_packet kprobe", prog1, timeout=8)

# ── Step 2: ovs_flow_tbl_lookup ──────────────────────────────────────────────
prog2 = """
kprobe:ovs_flow_tbl_lookup {
    printf("HIT ovs_flow_tbl_lookup\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(2, "ovs_flow_tbl_lookup kprobe", prog2, timeout=8)

# ── Step 3: ovs_execute_actions ──────────────────────────────────────────────
prog3 = """
kprobe:ovs_execute_actions {
    printf("HIT ovs_execute_actions\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(3, "ovs_execute_actions kprobe", prog3, timeout=8)

# ── Step 4: ovs_dp_upcall ───────────────────────────────────────────────────
prog4 = """
kprobe:ovs_dp_upcall {
    printf("HIT ovs_dp_upcall\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(4, "ovs_dp_upcall kprobe", prog4, timeout=8)

# ── Step 5: ovs_vport_receive ───────────────────────────────────────────────
prog5 = """
kprobe:ovs_vport_receive {
    printf("HIT ovs_vport_receive\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(5, "ovs_vport_receive kprobe", prog5, timeout=8)

# ── Step 6: ovs_vport_add ───────────────────────────────────────────────────
prog6 = """
kprobe:ovs_vport_add {
    printf("HIT ovs_vport_add\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(6, "ovs_vport_add kprobe", prog6, timeout=8)

# ── Step 7: ovs_dp_cmd_new ──────────────────────────────────────────────────
prog7 = """
kprobe:ovs_dp_cmd_new {
    printf("HIT ovs_dp_cmd_new\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(7, "ovs_dp_cmd_new kprobe", prog7, timeout=8)

# ── Step 8: ovs_flow_cmd_new ────────────────────────────────────────────────
prog8 = """
kprobe:ovs_flow_cmd_new {
    printf("HIT ovs_flow_cmd_new\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(8, "ovs_flow_cmd_new kprobe", prog8, timeout=8)

# ── Step 9: ovs_packet_cmd_execute ──────────────────────────────────────────
prog9 = """
kprobe:ovs_packet_cmd_execute {
    printf("HIT ovs_packet_cmd_execute\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(9, "ovs_packet_cmd_execute kprobe", prog9, timeout=8)

# ── Step 10: /sys/module/openvswitch ─────────────────────────────────────────
print(f"  Step 10: {'OvS module in /sys/module/openvswitch':50s}", end=" ")
ovs_present = False
try:
    if os.path.isdir("/sys/module/openvswitch"):
        ovs_present = True
    else:
        with open("/proc/kallsyms") as f:
            for line in f:
                if "ovs_dp_process_packet" in line:
                    ovs_present = True
                    break
except Exception:
    pass
if ovs_present:
    print(PASS)
    results.append((10, "OvS module present", PASS))
else:
    print(SKIP)
    results.append((10, "OvS module present", SKIP))

# ── Summary ──────────────────────────────────────────────────────────────────
print()
passed = sum(1 for _, _, s in results if s == PASS)
failed = sum(1 for _, _, s in results if s == FAIL)
skipped = sum(1 for _, _, s in results if s == SKIP)
total = len(results)
print(f"Results: {passed}/{total} passed, {failed} failed, {skipped} skipped")
if failed > 0:
    sys.exit(1)
