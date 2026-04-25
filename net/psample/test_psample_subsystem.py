#!/usr/bin/env python3
"""
BPFtrace-based tracing tests for the psample subsystem.

Tests cover packet sampling, group management, and netlink
message construction in net/psample/.
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

# ── Test 1: Trace psample packet sampling ──
check(
    "psample_sample_packet",
    'kprobe:psample_sample_packet { printf("sample_pkt group=%p skb=%p\\n", arg0, arg1); exit(); }',
    expect_attach=True
)

# ── Test 2: Trace psample group get ──
check(
    "psample_group_get",
    'kprobe:psample_group_get { printf("group_get net=%p group_num=%d\\n", arg0, arg1); exit(); }',
    expect_attach=True
)

# ── Test 3: Trace psample group put ──
check(
    "psample_group_put",
    'kprobe:psample_group_put { printf("group_put group=%p\\n", arg0); exit(); }',
    expect_attach=True
)

# ── Test 4: Trace psample group take ──
check(
    "psample_group_take",
    'kprobe:psample_group_take { printf("group_take group=%p\\n", arg0); exit(); }',
    expect_attach=True
)

# ── Test 5: Trace psample module init ──
check(
    "psample_module_init",
    'kprobe:psample_nl_init { printf("psample_nl_init\\n"); exit(); }',
    expect_attach=True
)

# ── Test 6: Trace tc act_sample action ──
check(
    "tcf_sample_act",
    'kprobe:tcf_sample_act { printf("tcf_sample skb=%p\\n", arg0); exit(); }',
    expect_attach=True
)

# ── Test 7: Trace tc act_sample init ──
check(
    "tcf_sample_init",
    'kprobe:tcf_sample_init { printf("tcf_sample_init net=%p\\n", arg0); exit(); }',
    expect_attach=True
)

# ── Test 8: Trace genetlink multicast for psample ──
check(
    "genlmsg_multicast_psample",
    'kprobe:genlmsg_multicast_netns { printf("genlmsg_mcast family=%p\\n", arg0); exit(); }',
    expect_attach=True
)

# ── Test 9: Trace psample tracepoint (if available) ──
check(
    "psample_tracepoint",
    'tracepoint:psample:psample_sample_packet { printf("tp:psample group=%d\\n", args->group_num); exit(); }',
    expect_attach=False
)

# ── Test 10: Trace skb clone in psample path ──
check(
    "psample_skb_clone",
    'kprobe:skb_clone { printf("skb_clone skb=%p\\n", arg0); exit(); }',
    expect_attach=True
)

# ── Summary ──
print(json.dumps(results, indent=2))
passed = sum(1 for r in results if r["result"] == "PASS")
failed = sum(1 for r in results if r["result"] == "FAIL")
skipped = sum(1 for r in results if r["result"] == "SKIP")
print(f"\nTotal: {len(results)} | PASS: {passed} | FAIL: {failed} | SKIP: {skipped}")
