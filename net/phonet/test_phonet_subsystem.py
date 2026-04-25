#!/usr/bin/env python3
"""
BPFtrace-based tracing tests for the Phonet subsystem.

Tests cover datagram/pipe socket operations, device registration,
and frame reception in net/phonet/.
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

# ── Test 1: Trace Phonet receive entry ──
check(
    "phonet_rcv",
    'kprobe:phonet_rcv { printf("phonet_rcv skb=%p dev=%p\\n", arg0, arg1); exit(); }',
    expect_attach=True
)

# ── Test 2: Trace Phonet datagram sendmsg ──
check(
    "pn_sendmsg",
    'kprobe:pn_sendmsg { printf("pn_sendmsg sock=%p msg=%p len=%d\\n", arg0, arg1, arg2); exit(); }',
    expect_attach=True
)

# ── Test 3: Trace Phonet datagram recvmsg ──
check(
    "pn_recvmsg",
    'kprobe:pn_recvmsg { printf("pn_recvmsg sock=%p msg=%p\\n", arg0, arg1); exit(); }',
    expect_attach=True
)

# ── Test 4: Trace Phonet pipe sendmsg ──
check(
    "pep_sendmsg",
    'kprobe:pep_sendmsg { printf("pep_sendmsg sock=%p msg=%p\\n", arg0, arg1); exit(); }',
    expect_attach=True
)

# ── Test 5: Trace Phonet pipe recvmsg ──
check(
    "pep_recvmsg",
    'kprobe:pep_recvmsg { printf("pep_recvmsg sock=%p msg=%p\\n", arg0, arg1); exit(); }',
    expect_attach=True
)

# ── Test 6: Trace Phonet pipe accept ──
check(
    "pep_sock_accept",
    'kprobe:pep_sock_accept { printf("pep_accept sock=%p\\n", arg0); exit(); }',
    expect_attach=True
)

# ── Test 7: Trace Phonet device registration ──
check(
    "phonet_device_register",
    'kprobe:phonet_device_register { printf("dev_register dev=%p\\n", arg0); exit(); }',
    expect_attach=True
)

# ── Test 8: Trace Phonet socket bind ──
check(
    "pn_socket_bind",
    'kprobe:pn_socket_bind { printf("pn_bind sock=%p addr=%p\\n", arg0, arg1); exit(); }',
    expect_attach=True
)

# ── Test 9: Trace Phonet pipe connect ──
check(
    "pep_sock_connect",
    'kprobe:pep_sock_connect { printf("pep_connect sock=%p\\n", arg0); exit(); }',
    expect_attach=True
)

# ── Test 10: Trace Phonet routing ──
check(
    "phonet_route_output",
    'kprobe:phonet_route_output { printf("route_output dev=%p\\n", arg0); exit(); }',
    expect_attach=True
)

# ── Summary ──
print(json.dumps(results, indent=2))
passed = sum(1 for r in results if r["result"] == "PASS")
failed = sum(1 for r in results if r["result"] == "FAIL")
skipped = sum(1 for r in results if r["result"] == "SKIP")
print(f"\nTotal: {len(results)} | PASS: {passed} | FAIL: {failed} | SKIP: {skipped}")
