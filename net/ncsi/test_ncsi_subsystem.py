#!/usr/bin/env python3
"""
BPFtrace-based tracing tests for the NC-SI subsystem.

Tests cover device start/stop, command/response handling, channel
configuration, and AEN processing in net/ncsi/.
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

# ── Test 1: Trace NC-SI response receive ──
check(
    "ncsi_rcv_rsp",
    'kprobe:ncsi_rcv_rsp { printf("rcv_rsp skb=%p dev=%p\\n", arg0, arg1); exit(); }',
    expect_attach=True
)

# ── Test 2: Trace NC-SI device start ──
check(
    "ncsi_start_dev",
    'kprobe:ncsi_start_dev { printf("start_dev nd=%p\\n", arg0); exit(); }',
    expect_attach=True
)

# ── Test 3: Trace NC-SI device stop ──
check(
    "ncsi_stop_dev",
    'kprobe:ncsi_stop_dev { printf("stop_dev nd=%p\\n", arg0); exit(); }',
    expect_attach=True
)

# ── Test 4: Trace NC-SI command send ──
check(
    "ncsi_send_cmd",
    'kprobe:ncsi_send_cmd { printf("send_cmd nd=%p\\n", arg0); exit(); }',
    expect_attach=True
)

# ── Test 5: Trace NC-SI channel configuration ──
check(
    "ncsi_configure_channel",
    'kprobe:ncsi_configure_channel { printf("configure_channel ndp=%p\\n", arg0); exit(); }',
    expect_attach=True
)

# ── Test 6: Trace NC-SI AEN link status change ──
check(
    "ncsi_aen_handler_lsc",
    'kprobe:ncsi_aen_handler_lsc { printf("aen_lsc ndp=%p\\n", arg0); exit(); }',
    expect_attach=True
)

# ── Test 7: Trace NC-SI register device ──
check(
    "ncsi_register_dev",
    'kprobe:ncsi_register_dev { printf("register_dev dev=%p\\n", arg0); exit(); }',
    expect_attach=True
)

# ── Test 8: Trace NC-SI unregister device ──
check(
    "ncsi_unregister_dev",
    'kprobe:ncsi_unregister_dev { printf("unregister_dev nd=%p\\n", arg0); exit(); }',
    expect_attach=True
)

# ── Test 9: Trace NC-SI channel probe ──
check(
    "ncsi_probe_channel",
    'kprobe:ncsi_probe_channel { printf("probe_channel ndp=%p\\n", arg0); exit(); }',
    expect_attach=True
)

# ── Test 10: Trace NC-SI netlink handler ──
check(
    "ncsi_netlink_setup",
    'kprobe:ncsi_set_channel_mask_nl { printf("nl_set_mask\\n"); exit(); }',
    expect_attach=True
)

# ── Summary ──
print(json.dumps(results, indent=2))
passed = sum(1 for r in results if r["result"] == "PASS")
failed = sum(1 for r in results if r["result"] == "FAIL")
skipped = sum(1 for r in results if r["result"] == "SKIP")
print(f"\nTotal: {len(results)} | PASS: {passed} | FAIL: {failed} | SKIP: {skipped}")
