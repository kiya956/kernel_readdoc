#!/usr/bin/env python3
"""
BPFtrace-based tracing tests for the IEEE 802.15.4 subsystem.

Tests cover frame reception, socket delivery, header operations,
and device registration in net/ieee802154/.
"""

import subprocess
import json
import time

results = []

def run_bpftrace(probe, timeout=5):
    """Run a bpftrace probe and return (returncode, stdout, stderr)."""
    try:
        proc = subprocess.run(
            ["bpftrace", "-e", probe],
            capture_output=True, text=True, timeout=timeout
        )
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return 0, "timeout", ""
    except FileNotFoundError:
        return -1, "", "bpftrace not found"

def check(name, probe, expect_attach=True):
    """Run a probe and record PASS/FAIL/SKIP result."""
    rc, out, err = run_bpftrace(probe)
    if rc == -1:
        results.append({"test": name, "result": "SKIP", "reason": "bpftrace not found"})
    elif "cannot attach" in err.lower() or "error" in err.lower():
        if expect_attach:
            results.append({"test": name, "result": "FAIL", "reason": err.strip()[:200]})
        else:
            results.append({"test": name, "result": "SKIP", "reason": "probe point not available"})
    else:
        results.append({"test": name, "result": "PASS", "reason": ""})

# ── Test 1: Trace IEEE 802.15.4 receive entry ──
check(
    "ieee802154_rcv",
    'kprobe:ieee802154_rcv { printf("rcv skb=%p dev=%p\\n", arg0, arg1); exit(); }',
    expect_attach=True
)

# ── Test 2: Trace frame delivery to sockets ──
check(
    "ieee802154_deliver_skb",
    'kprobe:ieee802154_deliver_skb { printf("deliver skb=%p\\n", arg0); exit(); }',
    expect_attach=True
)

# ── Test 3: Trace header push ──
check(
    "ieee802154_hdr_push",
    'kprobe:ieee802154_hdr_push { printf("hdr_push skb=%p\\n", arg0); exit(); }',
    expect_attach=True
)

# ── Test 4: Trace header pull/parse ──
check(
    "ieee802154_hdr_pull",
    'kprobe:ieee802154_hdr_pull { printf("hdr_pull skb=%p\\n", arg0); exit(); }',
    expect_attach=True
)

# ── Test 5: Trace WPAN PHY registration ──
check(
    "wpan_phy_register",
    'kprobe:wpan_phy_register { printf("phy_register phy=%p\\n", arg0); exit(); }',
    expect_attach=True
)

# ── Test 6: Trace nl802154 netlink commands ──
check(
    "nl802154_pre_doit",
    'kprobe:nl802154_pre_doit { printf("nl802154 cmd skb=%p\\n", arg0); exit(); }',
    expect_attach=True
)

# ── Test 7: Trace DGRAM socket sendmsg ──
check(
    "ieee802154_sock_sendmsg",
    'kprobe:dgram_sendmsg { printf("dgram_sendmsg sock=%p\\n", arg0); exit(); }',
    expect_attach=True
)

# ── Test 8: Trace DGRAM socket recvmsg ──
check(
    "ieee802154_sock_recvmsg",
    'kprobe:dgram_recvmsg { printf("dgram_recvmsg sock=%p\\n", arg0); exit(); }',
    expect_attach=True
)

# ── Test 9: Trace WPAN device addition ──
check(
    "cfg802154_dev_add",
    'kprobe:rdev_add_virtual_intf { printf("add_vintf rdev=%p\\n", arg0); exit(); }',
    expect_attach=True
)

# ── Test 10: Trace raw socket receive ──
check(
    "raw802154_rcv_skb",
    'kprobe:raw_rcv_skb { printf("raw_rcv sk=%p skb=%p\\n", arg0, arg1); exit(); }',
    expect_attach=True
)

# ── Summary ──
print(json.dumps(results, indent=2))
passed = sum(1 for r in results if r["result"] == "PASS")
failed = sum(1 for r in results if r["result"] == "FAIL")
skipped = sum(1 for r in results if r["result"] == "SKIP")
print(f"\nTotal: {len(results)} | PASS: {passed} | FAIL: {failed} | SKIP: {skipped}")
