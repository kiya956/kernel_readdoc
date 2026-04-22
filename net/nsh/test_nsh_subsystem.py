#!/usr/bin/env python3
"""
test_nsh_subsystem.py — bpftrace verification of the NSH subsystem.

NSH (Network Service Header, RFC 8300) provides metadata encapsulation for
Service Function Chaining (SFC).  The kernel's net/nsh module exposes
nsh_push() and nsh_pop() used by OvS and TC tunnels.

Steps
-----
1.  Check ETH_P_NSH (0x894F) registered in ptype list
2.  Probe nsh_push                  — NSH header add to skb
3.  Probe nsh_pop                   — NSH header remove from skb
4.  Probe nsh_hdr_len               — compute NSH header length
5.  Check nsh symbols in kallsyms
6.  Check OvS module loaded (main nsh_push/pop user)
7.  Probe ovs_execute_actions       — OvS action path triggers NSH ops
8.  Probe tun_p_to_eth_p            — NSH tunnel proto → Ethertype
9.  Check ETH_P_NSH in registered packet types (/proc/net/ptype)
10. Probe nsh_md2_push_tlv          — variable-length metadata push
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
ETH_P_NSH = 0x894F


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


print("\n=== NSH (Network Service Header) bpftrace verification ===\n")

# ── Step 1: ETH_P_NSH presence ───────────────────────────────────────────────
print(f"  Step  1: {'ETH_P_NSH=0x894F visible in ptype or kallsyms':50s}", end=" ")
nsh_present = False
try:
    with open("/proc/kallsyms") as f:
        for line in f:
            if "nsh_push" in line or "nsh_pop" in line or "eth_p_nsh" in line.lower():
                nsh_present = True
                break
except Exception:
    pass
if nsh_present:
    print(PASS)
    results.append((1, "NSH symbols", PASS))
else:
    print(SKIP)
    results.append((1, "NSH symbols", SKIP))

# ── Step 2: nsh_push ─────────────────────────────────────────────────────────
prog2 = """
kprobe:nsh_push {
    printf("HIT nsh_push\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(2, "nsh_push kprobe", prog2, timeout=8)

# ── Step 3: nsh_pop ──────────────────────────────────────────────────────────
prog3 = """
kprobe:nsh_pop {
    printf("HIT nsh_pop\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(3, "nsh_pop kprobe", prog3, timeout=8)

# ── Step 4: nsh_hdr_len ──────────────────────────────────────────────────────
prog4 = """
kprobe:nsh_hdr_len {
    printf("HIT nsh_hdr_len\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(4, "nsh_hdr_len kprobe", prog4, timeout=8)

# ── Step 5: NSH in kallsyms ──────────────────────────────────────────────────
print(f"  Step  5: {'nsh_push in /proc/kallsyms':50s}", end=" ")
nsh_ksym = False
try:
    with open("/proc/kallsyms") as f:
        for line in f:
            if "nsh_push" in line:
                nsh_ksym = True
                break
except Exception:
    pass
if nsh_ksym:
    print(PASS)
    results.append((5, "nsh kallsyms", PASS))
else:
    print(SKIP)
    results.append((5, "nsh kallsyms", SKIP))

# ── Step 6: OvS loaded ───────────────────────────────────────────────────────
print(f"  Step  6: {'openvswitch module loaded':50s}", end=" ")
ovs_loaded = False
try:
    r = subprocess.run(["lsmod"], capture_output=True, text=True)
    ovs_loaded = "openvswitch" in r.stdout
except Exception:
    pass
if ovs_loaded:
    print(PASS)
    results.append((6, "OvS loaded", PASS))
else:
    print(SKIP)
    results.append((6, "OvS loaded", SKIP))

# ── Step 7: ovs_execute_actions ──────────────────────────────────────────────
prog7 = """
kprobe:ovs_execute_actions {
    printf("HIT ovs_execute_actions\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(7, "ovs_execute_actions kprobe", prog7, timeout=8)

# ── Step 8: tun_p_to_eth_p ───────────────────────────────────────────────────
prog8 = """
kprobe:tun_p_to_eth_p {
    printf("HIT tun_p_to_eth_p\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(8, "tun_p_to_eth_p kprobe", prog8, timeout=8)

# ── Step 9: /proc/net/ptype check ────────────────────────────────────────────
print(f"  Step  9: {'ETH_P_NSH in /proc/net/ptype':50s}", end=" ")
ptype_ok = False
try:
    with open("/proc/net/ptype") as f:
        for line in f:
            if "894f" in line.lower() or "8950" in line.lower():
                ptype_ok = True
                break
except Exception:
    pass
if ptype_ok:
    print(PASS)
    results.append((9, "ptype NSH", PASS))
else:
    print(SKIP)
    results.append((9, "ptype NSH", SKIP))

# ── Step 10: nsh_md2_push_tlv ────────────────────────────────────────────────
prog10 = """
kprobe:nsh_md2_push_tlv {
    printf("HIT nsh_md2_push_tlv\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(10, "nsh_md2_push_tlv kprobe", prog10, timeout=8)

# ── Summary ──────────────────────────────────────────────────────────────────
print()
passed = sum(1 for _, _, s in results if s == PASS)
failed = sum(1 for _, _, s in results if s == FAIL)
skipped = sum(1 for _, _, s in results if s == SKIP)
total = len(results)
print(f"Results: {passed}/{total} passed, {failed} failed, {skipped} skipped")
if failed > 0:
    sys.exit(1)
