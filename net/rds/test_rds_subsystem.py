#!/usr/bin/env python3
"""
BPFtrace-based tracing tests for the RDS (Reliable Datagram Sockets) subsystem.

Tests cover send/receive paths, connection management, transport
operations, and RDMA integration in net/rds/.
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

# ── Test 1: Trace RDS sendmsg ──
check(
    "rds_sendmsg",
    'kprobe:rds_sendmsg { printf("rds_sendmsg sock=%p msg=%p len=%d\\n", arg0, arg1, arg2); exit(); }',
    expect_attach=True
)

# ── Test 2: Trace RDS recvmsg ──
check(
    "rds_recvmsg",
    'kprobe:rds_recvmsg { printf("rds_recvmsg sock=%p msg=%p\\n", arg0, arg1); exit(); }',
    expect_attach=True
)

# ── Test 3: Trace RDS incoming message processing ──
check(
    "rds_recv_incoming",
    'kprobe:rds_recv_incoming { printf("recv_incoming conn=%p\\n", arg0); exit(); }',
    expect_attach=True
)

# ── Test 4: Trace RDS send transmit ──
check(
    "rds_send_xmit",
    'kprobe:rds_send_xmit { printf("send_xmit conn=%p\\n", arg0); exit(); }',
    expect_attach=True
)

# ── Test 5: Trace RDS connection create ──
check(
    "rds_conn_create",
    'kprobe:rds_conn_create { printf("conn_create\\n"); exit(); }',
    expect_attach=True
)

# ── Test 6: Trace RDS connection complete ──
check(
    "rds_connect_complete",
    'kprobe:rds_connect_complete { printf("conn_complete conn=%p\\n", arg0); exit(); }',
    expect_attach=True
)

# ── Test 7: Trace RDS socket bind ──
check(
    "rds_bind",
    'kprobe:rds_bind { printf("rds_bind sock=%p addr=%p\\n", arg0, arg1); exit(); }',
    expect_attach=True
)

# ── Test 8: Trace RDS socket release ──
check(
    "rds_release",
    'kprobe:rds_release { printf("rds_release sock=%p\\n", arg0); exit(); }',
    expect_attach=True
)

# ── Test 9: Trace RDS message allocation ──
check(
    "rds_message_alloc",
    'kprobe:rds_message_alloc { printf("msg_alloc nents=%d\\n", arg0); exit(); }',
    expect_attach=True
)

# ── Test 10: Trace RDS congestion update ──
check(
    "rds_cong_updated",
    'kprobe:rds_cong_updated { printf("cong_updated conn=%p\\n", arg0); exit(); }',
    expect_attach=True
)

# ── Summary ──
print(json.dumps(results, indent=2))
passed = sum(1 for r in results if r["result"] == "PASS")
failed = sum(1 for r in results if r["result"] == "FAIL")
skipped = sum(1 for r in results if r["result"] == "SKIP")
print(f"\nTotal: {len(results)} | PASS: {passed} | FAIL: {failed} | SKIP: {skipped}")
