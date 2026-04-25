#!/usr/bin/env python3
"""
BPFtrace-based tracing tests for the ROSE (X.25 over AX.25) subsystem.

Tests cover virtual circuit management, socket operations, routing,
and frame handling in net/rose/.
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

# ── Test 1: Trace ROSE receive entry ──
check(
    "rose_rcv",
    'kprobe:rose_rcv { printf("rose_rcv skb=%p\\n", arg0); exit(); }',
    expect_attach=True
)

# ── Test 2: Trace ROSE sendmsg ──
check(
    "rose_sendmsg",
    'kprobe:rose_sendmsg { printf("rose_sendmsg sock=%p msg=%p len=%d\\n", arg0, arg1, arg2); exit(); }',
    expect_attach=True
)

# ── Test 3: Trace ROSE recvmsg ──
check(
    "rose_recvmsg",
    'kprobe:rose_recvmsg { printf("rose_recvmsg sock=%p msg=%p\\n", arg0, arg1); exit(); }',
    expect_attach=True
)

# ── Test 4: Trace ROSE connect (call request) ──
check(
    "rose_connect",
    'kprobe:rose_connect { printf("rose_connect sock=%p\\n", arg0); exit(); }',
    expect_attach=True
)

# ── Test 5: Trace ROSE route frame ──
check(
    "rose_route_frame",
    'kprobe:rose_route_frame { printf("route_frame skb=%p\\n", arg0); exit(); }',
    expect_attach=True
)

# ── Test 6: Trace ROSE kick (send queued data) ──
check(
    "rose_kick",
    'kprobe:rose_kick { printf("rose_kick sk=%p\\n", arg0); exit(); }',
    expect_attach=True
)

# ── Test 7: Trace ROSE socket release ──
check(
    "rose_release",
    'kprobe:rose_release { printf("rose_release sock=%p\\n", arg0); exit(); }',
    expect_attach=True
)

# ── Test 8: Trace ROSE socket bind ──
check(
    "rose_bind",
    'kprobe:rose_bind { printf("rose_bind sock=%p addr=%p\\n", arg0, arg1); exit(); }',
    expect_attach=True
)

# ── Test 9: Trace ROSE timer expiry ──
check(
    "rose_timer_expiry",
    'kprobe:rose_timer_expiry { printf("rose_timer timer=%p\\n", arg0); exit(); }',
    expect_attach=True
)

# ── Test 10: Trace ROSE heartbeat ──
check(
    "rose_heartbeat_expiry",
    'kprobe:rose_heartbeat_expiry { printf("rose_heartbeat timer=%p\\n", arg0); exit(); }',
    expect_attach=True
)

# ── Summary ──
print(json.dumps(results, indent=2))
passed = sum(1 for r in results if r["result"] == "PASS")
failed = sum(1 for r in results if r["result"] == "FAIL")
skipped = sum(1 for r in results if r["result"] == "SKIP")
print(f"\nTotal: {len(results)} | PASS: {passed} | FAIL: {failed} | SKIP: {skipped}")
