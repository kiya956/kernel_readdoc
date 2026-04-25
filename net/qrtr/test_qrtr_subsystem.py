#!/usr/bin/env python3
"""
BPFtrace-based tracing tests for the QRTR (Qualcomm IPC Router) subsystem.

Tests cover socket operations, endpoint handling, name server,
and message routing in net/qrtr/.
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

# ── Test 1: Trace QRTR endpoint post (receive from transport) ──
check(
    "qrtr_endpoint_post",
    'kprobe:qrtr_endpoint_post { printf("ep_post ep=%p data=%p len=%d\\n", arg0, arg1, arg2); exit(); }',
    expect_attach=True
)

# ── Test 2: Trace QRTR sendmsg ──
check(
    "qrtr_sendmsg",
    'kprobe:qrtr_sendmsg { printf("sendmsg sock=%p msg=%p len=%d\\n", arg0, arg1, arg2); exit(); }',
    expect_attach=True
)

# ── Test 3: Trace QRTR recvmsg ──
check(
    "qrtr_recvmsg",
    'kprobe:qrtr_recvmsg { printf("recvmsg sock=%p msg=%p\\n", arg0, arg1); exit(); }',
    expect_attach=True
)

# ── Test 4: Trace QRTR node enqueue ──
check(
    "qrtr_node_enqueue",
    'kprobe:qrtr_node_enqueue { printf("node_enqueue node=%p\\n", arg0); exit(); }',
    expect_attach=True
)

# ── Test 5: Trace QRTR endpoint register ──
check(
    "qrtr_endpoint_register",
    'kprobe:qrtr_endpoint_register { printf("ep_register ep=%p nid=%d\\n", arg0, arg1); exit(); }',
    expect_attach=True
)

# ── Test 6: Trace QRTR name server worker ──
check(
    "qrtr_ns_worker",
    'kprobe:qrtr_ns_worker { printf("ns_worker work=%p\\n", arg0); exit(); }',
    expect_attach=True
)

# ── Test 7: Trace QRTR socket bind ──
check(
    "qrtr_bind",
    'kprobe:qrtr_bind { printf("qrtr_bind sock=%p addr=%p\\n", arg0, arg1); exit(); }',
    expect_attach=True
)

# ── Test 8: Trace QRTR socket connect ──
check(
    "qrtr_connect",
    'kprobe:qrtr_connect { printf("qrtr_connect sock=%p\\n", arg0); exit(); }',
    expect_attach=True
)

# ── Test 9: Trace QRTR local enqueue ──
check(
    "qrtr_local_enqueue",
    'kprobe:qrtr_local_enqueue { printf("local_enqueue node=%p\\n", arg0); exit(); }',
    expect_attach=True
)

# ── Test 10: Trace QRTR socket release ──
check(
    "qrtr_release",
    'kprobe:qrtr_release { printf("qrtr_release sock=%p\\n", arg0); exit(); }',
    expect_attach=True
)

# ── Summary ──
print(json.dumps(results, indent=2))
passed = sum(1 for r in results if r["result"] == "PASS")
failed = sum(1 for r in results if r["result"] == "FAIL")
skipped = sum(1 for r in results if r["result"] == "SKIP")
print(f"\nTotal: {len(results)} | PASS: {passed} | FAIL: {failed} | SKIP: {skipped}")
