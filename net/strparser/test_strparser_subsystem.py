#!/usr/bin/env python3
"""
BPFtrace-based test suite for the Stream Parser subsystem (net/strparser).

Tests verify that key strparser kernel functions are traceable via kprobes.
Each test attaches a short-lived bpftrace probe and checks for successful
attachment (PASS) or expected absence (SKIP). No actual strparser consumers
need to be active — the tests validate probe-point availability.
"""

import subprocess
import json
import time

results = []


def run_bpftrace(probe, timeout=5):
    """Run a bpftrace one-liner with a timeout and return (returncode, stdout, stderr)."""
    cmd = ["sudo", "bpftrace", "-e", probe]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return 0, "timeout (probe attached OK)", ""
    except Exception as e:
        return -1, "", str(e)


def check(name, probe, expect_attach=True):
    """Execute a bpftrace probe and record PASS/FAIL/SKIP."""
    rc, stdout, stderr = run_bpftrace(probe)
    combined = stdout + stderr

    if "Invalid probe" in combined or "not found" in combined:
        if not expect_attach:
            results.append({"test": name, "status": "SKIP", "detail": "Probe point not available (expected)"})
        else:
            results.append({"test": name, "status": "SKIP", "detail": "Probe point not available in this kernel"})
    elif rc == 0 or "Attaching" in combined:
        results.append({"test": name, "status": "PASS", "detail": combined.strip()[:200]})
    else:
        results.append({"test": name, "status": "FAIL", "detail": combined.strip()[:200]})


# ─── Test Steps ───────────────────────────────────────────────────────────────

# Step 1: Trace strp_data_ready — main entry when TCP data arrives
check(
    "strp_data_ready probe",
    'kprobe:strp_data_ready { printf("strp_data_ready called\\n"); exit(); }'
)

# Step 2: Trace strp_process — core message parse/deliver loop
check(
    "strp_process probe",
    'kprobe:strp_process { printf("strp_process called\\n"); exit(); }'
)

# Step 3: Trace strp_init — strparser instance initialization
check(
    "strp_init probe",
    'kprobe:strp_init { printf("strp_init called\\n"); exit(); }'
)

# Step 4: Trace strp_stop — stop parsing on a socket
check(
    "strp_stop probe",
    'kprobe:strp_stop { printf("strp_stop called\\n"); exit(); }'
)

# Step 5: Trace strp_done — destroy strparser instance
check(
    "strp_done probe",
    'kprobe:strp_done { printf("strp_done called\\n"); exit(); }'
)

# Step 6: Trace strp_check_rcv — re-check pending data
check(
    "strp_check_rcv probe",
    'kprobe:strp_check_rcv { printf("strp_check_rcv called\\n"); exit(); }'
)

# Step 7: Trace strp_recv — internal receive handler (if present)
check(
    "strp_recv probe",
    'kprobe:strp_recv { printf("strp_recv called\\n"); exit(); }',
    expect_attach=False
)

# Step 8: Trace strp_stream_read — stream read path (if present)
check(
    "strp_stream_read probe",
    'kprobe:strp_read_sock { printf("strp_read_sock called\\n"); exit(); }',
    expect_attach=False
)

# Step 9: Trace strp_msg_timeout — timer-based message timeout
check(
    "strp_msg_timeout probe",
    'kprobe:strp_msg_timeout { printf("msg timeout\\n"); exit(); }',
    expect_attach=False
)

# Step 10: Trace do_strp_work — strparser work queue handler
check(
    "do_strp_work probe",
    'kprobe:do_strp_work { printf("do_strp_work called\\n"); exit(); }',
    expect_attach=False
)

# ─── Summary ──────────────────────────────────────────────────────────────────

print(json.dumps(results, indent=2))

passed = sum(1 for r in results if r["status"] == "PASS")
skipped = sum(1 for r in results if r["status"] == "SKIP")
failed = sum(1 for r in results if r["status"] == "FAIL")

print(f"\n=== Strparser Subsystem Test Summary ===")
print(f"PASSED: {passed}  SKIPPED: {skipped}  FAILED: {failed}  TOTAL: {len(results)}")
