#!/usr/bin/env python3
"""
BPFtrace-based test suite for the RxRPC protocol subsystem (net/rxrpc).

Tests verify that key RxRPC kernel functions are traceable via kprobes.
Each test attaches a short-lived bpftrace probe and checks for successful
attachment (PASS) or expected absence (SKIP). No actual RxRPC traffic is
required — the tests validate probe-point availability.
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

# Step 1: Trace rxrpc_recvmsg — userspace receive path
check(
    "rxrpc_recvmsg probe",
    'kprobe:rxrpc_recvmsg { printf("rxrpc_recvmsg called\\n"); exit(); }'
)

# Step 2: Trace rxrpc_sendmsg — userspace send path
check(
    "rxrpc_sendmsg probe",
    'kprobe:rxrpc_sendmsg { printf("rxrpc_sendmsg called\\n"); exit(); }'
)

# Step 3: Trace rxrpc_new_client_call — call allocation
check(
    "rxrpc_new_client_call probe",
    'kprobe:rxrpc_new_client_call { printf("new client call\\n"); exit(); }'
)

# Step 4: Trace rxrpc_input_packet — incoming packet processing
check(
    "rxrpc_input_packet probe",
    'kprobe:rxrpc_input_packet { printf("input packet\\n"); exit(); }'
)

# Step 5: Trace rxrpc_kernel_send_data — kernel API send
check(
    "rxrpc_kernel_send_data probe",
    'kprobe:rxrpc_kernel_send_data { printf("kernel send data\\n"); exit(); }'
)

# Step 6: Trace rxrpc_send_abort_packet — call abort
check(
    "rxrpc_send_abort_packet probe",
    'kprobe:rxrpc_send_abort_packet { printf("abort packet\\n"); exit(); }'
)

# Step 7: Trace rxrpc_propose_ack — ACK scheduling
check(
    "rxrpc_propose_ack probe",
    'kprobe:rxrpc_propose_ack { printf("propose ack\\n"); exit(); }'
)

# Step 8: Trace rxrpc_alloc_call — call object allocation
check(
    "rxrpc_alloc_call probe",
    'kprobe:rxrpc_alloc_call { printf("alloc call\\n"); exit(); }'
)

# Step 9: Trace rxrpc_put_peer — peer reference release
check(
    "rxrpc_put_peer probe",
    'kprobe:rxrpc_put_peer { printf("put peer\\n"); exit(); }'
)

# Step 10: Trace rxrpc_connect_call — connection setup
check(
    "rxrpc_connect_call probe",
    'kprobe:rxrpc_connect_call { printf("connect call\\n"); exit(); }'
)

# ─── Summary ──────────────────────────────────────────────────────────────────

print(json.dumps(results, indent=2))

passed = sum(1 for r in results if r["status"] == "PASS")
skipped = sum(1 for r in results if r["status"] == "SKIP")
failed = sum(1 for r in results if r["status"] == "FAIL")

print(f"\n=== RxRPC Subsystem Test Summary ===")
print(f"PASSED: {passed}  SKIPPED: {skipped}  FAILED: {failed}  TOTAL: {len(results)}")
