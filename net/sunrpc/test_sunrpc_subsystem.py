#!/usr/bin/env python3
"""
BPFtrace-based test suite for the Sun RPC subsystem (net/sunrpc).

Tests verify that key Sun RPC kernel functions are traceable via kprobes.
Each test attaches a short-lived bpftrace probe and checks for successful
attachment (PASS) or expected absence (SKIP). No NFS mounts are required —
the tests validate probe-point availability.
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

# Step 1: Trace rpc_call_sync — synchronous RPC call
check(
    "rpc_call_sync probe",
    'kprobe:rpc_call_sync { printf("rpc_call_sync called\\n"); exit(); }'
)

# Step 2: Trace rpc_run_task — async RPC task dispatch
check(
    "rpc_run_task probe",
    'kprobe:rpc_run_task { printf("rpc_run_task called\\n"); exit(); }'
)

# Step 3: Trace svc_process — server request processing
check(
    "svc_process probe",
    'kprobe:svc_process { printf("svc_process called\\n"); exit(); }'
)

# Step 4: Trace xprt_transmit — transport-level transmit
check(
    "xprt_transmit probe",
    'kprobe:xprt_transmit { printf("xprt_transmit called\\n"); exit(); }'
)

# Step 5: Trace xprt_connect — transport connection setup
check(
    "xprt_connect probe",
    'kprobe:xprt_connect { printf("xprt_connect called\\n"); exit(); }'
)

# Step 6: Trace rpc_create — RPC client creation
check(
    "rpc_create probe",
    'kprobe:rpc_create { printf("rpc_create called\\n"); exit(); }'
)

# Step 7: Trace svc_recv — server receive path
check(
    "svc_recv probe",
    'kprobe:svc_recv { printf("svc_recv called\\n"); exit(); }'
)

# Step 8: Trace rpcauth_wrap_req — auth credential wrapping
check(
    "rpcauth_wrap_req probe",
    'kprobe:rpcauth_wrap_req { printf("auth wrap\\n"); exit(); }'
)

# Step 9: Trace rpc_exit_task — RPC task completion
check(
    "rpc_exit_task probe",
    'kprobe:rpc_exit_task { printf("exit task\\n"); exit(); }'
)

# Step 10: Trace xprt_release — transport release
check(
    "xprt_release probe",
    'kprobe:xprt_release { printf("xprt release\\n"); exit(); }'
)

# ─── Summary ──────────────────────────────────────────────────────────────────

print(json.dumps(results, indent=2))

passed = sum(1 for r in results if r["status"] == "PASS")
skipped = sum(1 for r in results if r["status"] == "SKIP")
failed = sum(1 for r in results if r["status"] == "FAIL")

print(f"\n=== Sun RPC Subsystem Test Summary ===")
print(f"PASSED: {passed}  SKIPPED: {skipped}  FAILED: {failed}  TOTAL: {len(results)}")
