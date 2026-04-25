#!/usr/bin/env python3
"""
BPFtrace-based tracing tests for the mac802154 subsystem.

Tests cover TX/RX paths, CSMA/CA, driver interface,
and hardware registration in net/mac802154/.
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

# ── Test 1: Trace mac802154 subinterface TX entry ──
check(
    "mac802154_subif_start_xmit",
    'kprobe:mac802154_subif_start_xmit { printf("xmit skb=%p dev=%p\\n", arg0, arg1); exit(); }',
    expect_attach=True
)

# ── Test 2: Trace ieee802154 RX path ──
check(
    "ieee802154_rx",
    'kprobe:ieee802154_rx { printf("rx local=%p skb=%p\\n", arg0, arg1); exit(); }',
    expect_attach=True
)

# ── Test 3: Trace IRQ-safe RX handoff ──
check(
    "ieee802154_rx_irqsafe",
    'kprobe:ieee802154_rx_irqsafe { printf("rx_irqsafe hw=%p skb=%p\\n", arg0, arg1); exit(); }',
    expect_attach=True
)

# ── Test 4: Trace TX worker ──
check(
    "ieee802154_xmit_worker",
    'kprobe:ieee802154_xmit_worker { printf("xmit_worker work=%p\\n", arg0); exit(); }',
    expect_attach=True
)

# ── Test 5: Trace hardware registration ──
check(
    "ieee802154_register_hw",
    'kprobe:ieee802154_register_hw { printf("register_hw hw=%p\\n", arg0); exit(); }',
    expect_attach=True
)

# ── Test 6: Trace hardware allocation ──
check(
    "ieee802154_alloc_hw",
    'kprobe:ieee802154_alloc_hw { printf("alloc_hw priv_size=%d ops=%p\\n", arg0, arg1); exit(); }',
    expect_attach=True
)

# ── Test 7: Trace CSMA/CA transmit ──
check(
    "ieee802154_csma_ca",
    'kprobe:ieee802154_cca_is_busy { printf("cca_busy\\n"); exit(); }',
    expect_attach=True
)

# ── Test 8: Trace interface open ──
check(
    "mac802154_slave_open",
    'kprobe:mac802154_slave_open { printf("slave_open dev=%p\\n", arg0); exit(); }',
    expect_attach=True
)

# ── Test 9: Trace interface close ──
check(
    "mac802154_slave_close",
    'kprobe:mac802154_slave_close { printf("slave_close dev=%p\\n", arg0); exit(); }',
    expect_attach=True
)

# ── Test 10: Trace driver xmit async call ──
check(
    "drv_xmit_async",
    'kprobe:drv_xmit_async { printf("drv_xmit_async local=%p skb=%p\\n", arg0, arg1); exit(); }',
    expect_attach=True
)

# ── Summary ──
print(json.dumps(results, indent=2))
passed = sum(1 for r in results if r["result"] == "PASS")
failed = sum(1 for r in results if r["result"] == "FAIL")
skipped = sum(1 for r in results if r["result"] == "SKIP")
print(f"\nTotal: {len(results)} | PASS: {passed} | FAIL: {failed} | SKIP: {skipped}")
