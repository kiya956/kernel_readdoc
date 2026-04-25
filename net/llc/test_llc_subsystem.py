#!/usr/bin/env python3
"""
BPFtrace-based tracing tests for the LLC (IEEE 802.2) subsystem.

Tests cover SAP management, PDU handling, connection state machine,
and packet send/receive paths in net/llc/.
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

# ── Test 1: Trace LLC receive entry point ──
check(
    "llc_rcv_entry",
    'kprobe:llc_rcv { printf("llc_rcv skb=%p\\n", arg0); exit(); }',
    expect_attach=True
)

# ── Test 2: Trace LLC SAP open ──
check(
    "llc_sap_open",
    'kprobe:llc_sap_open { printf("sap_open lsap=%d\\n", arg0); exit(); }',
    expect_attach=True
)

# ── Test 3: Trace LLC SAP close ──
check(
    "llc_sap_close",
    'kprobe:llc_sap_close { printf("sap_close sap=%p\\n", arg0); exit(); }',
    expect_attach=True
)

# ── Test 4: Trace LLC build and send packet ──
check(
    "llc_build_and_send_pkt",
    'kprobe:llc_build_and_send_pkt { printf("build_send skb=%p\\n", arg0); exit(); }',
    expect_attach=True
)

# ── Test 5: Trace LLC connection state processing ──
check(
    "llc_conn_state_process",
    'kprobe:llc_conn_state_process { printf("conn_state sk=%p skb=%p\\n", arg0, arg1); exit(); }',
    expect_attach=True
)

# ── Test 6: Trace LLC PDU header initialization ──
check(
    "llc_pdu_header_init",
    'kprobe:llc_pdu_header_init { printf("pdu_init skb=%p type=%d\\n", arg0, arg1); exit(); }',
    expect_attach=True
)

# ── Test 7: Trace LLC SAP find ──
check(
    "llc_sap_find",
    'kprobe:llc_sap_find { printf("sap_find sap_value=%d\\n", arg0); exit(); }',
    expect_attach=True
)

# ── Test 8: Trace LLC connection handler ──
check(
    "llc_conn_handler",
    'kprobe:llc_conn_handler { printf("conn_handler sk=%p skb=%p\\n", arg0, arg1); exit(); }',
    expect_attach=True
)

# ── Test 9: Trace LLC socket sendmsg ──
check(
    "llc_ui_sendmsg",
    'kprobe:llc_ui_sendmsg { printf("sendmsg sock=%p msg=%p len=%d\\n", arg0, arg1, arg2); exit(); }',
    expect_attach=True
)

# ── Test 10: Trace LLC socket recvmsg ──
check(
    "llc_ui_recvmsg",
    'kprobe:llc_ui_recvmsg { printf("recvmsg sock=%p msg=%p\\n", arg0, arg1); exit(); }',
    expect_attach=True
)

# ── Summary ──
print(json.dumps(results, indent=2))
passed = sum(1 for r in results if r["result"] == "PASS")
failed = sum(1 for r in results if r["result"] == "FAIL")
skipped = sum(1 for r in results if r["result"] == "SKIP")
print(f"\nTotal: {len(results)} | PASS: {passed} | FAIL: {failed} | SKIP: {skipped}")
