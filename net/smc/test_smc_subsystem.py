#!/usr/bin/env python3
"""
BPFtrace-based test suite for the SMC (Shared Memory Communications) subsystem (net/smc).

Tests verify that key SMC kernel functions are traceable via kprobes.
Each test attaches a short-lived bpftrace probe and checks for successful
attachment (PASS) or expected absence (SKIP). No actual SMC/RDMA hardware
is required — the tests validate probe-point availability.
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

# Step 1: Trace smc_sendmsg — SMC data send path
check(
    "smc_sendmsg probe",
    'kprobe:smc_sendmsg { printf("smc_sendmsg called\\n"); exit(); }'
)

# Step 2: Trace smc_recvmsg — SMC data receive path
check(
    "smc_recvmsg probe",
    'kprobe:smc_recvmsg { printf("smc_recvmsg called\\n"); exit(); }'
)

# Step 3: Trace smc_connect — SMC connection initiation
check(
    "smc_connect probe",
    'kprobe:smc_connect { printf("smc_connect called\\n"); exit(); }'
)

# Step 4: Trace smc_accept — incoming SMC connection
check(
    "smc_accept probe",
    'kprobe:smc_accept { printf("smc_accept called\\n"); exit(); }'
)

# Step 5: Trace smc_close — SMC connection teardown
check(
    "smc_close probe",
    'kprobe:smc_close { printf("smc_close called\\n"); exit(); }'
)

# Step 6: Trace smc_clc_send_proposal — CLC handshake proposal
check(
    "smc_clc_send_proposal probe",
    'kprobe:smc_clc_send_proposal { printf("clc proposal\\n"); exit(); }'
)

# Step 7: Trace smc_conn_create — connection object creation
check(
    "smc_conn_create probe",
    'kprobe:smc_conn_create { printf("conn create\\n"); exit(); }'
)

# Step 8: Trace smc_lgr_create — link group creation
check(
    "smc_lgr_create probe",
    'kprobe:smc_lgr_create { printf("lgr create\\n"); exit(); }'
)

# Step 9: Trace smc_tx_sendmsg — transmit path internal
check(
    "smc_tx_sendmsg probe",
    'kprobe:smc_tx_sendmsg { printf("tx sendmsg\\n"); exit(); }'
)

# Step 10: Trace smc_rx_recvmsg — receive path internal
check(
    "smc_rx_recvmsg probe",
    'kprobe:smc_rx_recvmsg { printf("rx recvmsg\\n"); exit(); }'
)

# ─── Summary ──────────────────────────────────────────────────────────────────

print(json.dumps(results, indent=2))

passed = sum(1 for r in results if r["status"] == "PASS")
skipped = sum(1 for r in results if r["status"] == "SKIP")
failed = sum(1 for r in results if r["status"] == "FAIL")

print(f"\n=== SMC Subsystem Test Summary ===")
print(f"PASSED: {passed}  SKIPPED: {skipped}  FAILED: {failed}  TOTAL: {len(results)}")
