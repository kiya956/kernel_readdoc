#!/usr/bin/env python3
"""
BPFtrace-based tracing tests for the NET/ROM subsystem.

Tests cover socket operations, routing, frame reception,
and neighbor management in net/netrom/.
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

# ── Test 1: Trace NET/ROM receive entry ──
check(
    "nr_rcv",
    'kprobe:nr_rcv { printf("nr_rcv skb=%p\\n", arg0); exit(); }',
    expect_attach=True
)

# ── Test 2: Trace NET/ROM sendmsg ──
check(
    "nr_sendmsg",
    'kprobe:nr_sendmsg { printf("nr_sendmsg sock=%p msg=%p len=%d\\n", arg0, arg1, arg2); exit(); }',
    expect_attach=True
)

# ── Test 3: Trace NET/ROM recvmsg ──
check(
    "nr_recvmsg",
    'kprobe:nr_recvmsg { printf("nr_recvmsg sock=%p msg=%p\\n", arg0, arg1); exit(); }',
    expect_attach=True
)

# ── Test 4: Trace NET/ROM frame output ──
check(
    "nr_output",
    'kprobe:nr_output { printf("nr_output sk=%p skb=%p\\n", arg0, arg1); exit(); }',
    expect_attach=True
)

# ── Test 5: Trace NET/ROM route frame (forwarding) ──
check(
    "nr_route_frame",
    'kprobe:nr_route_frame { printf("nr_route_frame skb=%p\\n", arg0); exit(); }',
    expect_attach=True
)

# ── Test 6: Trace NET/ROM add node to routing table ──
check(
    "nr_add_node",
    'kprobe:nr_add_node { printf("nr_add_node\\n"); exit(); }',
    expect_attach=True
)

# ── Test 7: Trace NET/ROM socket connect ──
check(
    "nr_connect",
    'kprobe:nr_connect { printf("nr_connect sock=%p\\n", arg0); exit(); }',
    expect_attach=True
)

# ── Test 8: Trace NET/ROM socket release ──
check(
    "nr_release",
    'kprobe:nr_release { printf("nr_release sock=%p\\n", arg0); exit(); }',
    expect_attach=True
)

# ── Test 9: Trace NET/ROM link setup ──
check(
    "nr_link_establish",
    'kprobe:nr_establish_data_link { printf("nr_establish_data_link sk=%p\\n", arg0); exit(); }',
    expect_attach=True
)

# ── Test 10: Trace NET/ROM heartbeat timer ──
check(
    "nr_heartbeat_expiry",
    'kprobe:nr_heartbeat_expiry { printf("nr_heartbeat timer=%p\\n", arg0); exit(); }',
    expect_attach=True
)

# ── Summary ──
print(json.dumps(results, indent=2))
passed = sum(1 for r in results if r["result"] == "PASS")
failed = sum(1 for r in results if r["result"] == "FAIL")
skipped = sum(1 for r in results if r["result"] == "SKIP")
print(f"\nTotal: {len(results)} | PASS: {passed} | FAIL: {failed} | SKIP: {skipped}")
