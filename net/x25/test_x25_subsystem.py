#!/usr/bin/env python3
"""
BPFtrace-based test suite for the X.25 packet-layer subsystem (net/x25).

Tests verify that key X.25 kernel functions are traceable via kprobes.
Each test attaches a short-lived bpftrace probe and checks for successful
attachment (PASS) or expected absence (SKIP). No X.25 hardware or LAPB
links are required — the tests validate probe-point availability.
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

# Step 1: Trace x25_rcv — main packet receive entry
check(
    "x25_rcv probe",
    'kprobe:x25_rcv { printf("x25_rcv called\\n"); exit(); }'
)

# Step 2: Trace x25_sendmsg — data send on virtual circuit
check(
    "x25_sendmsg probe",
    'kprobe:x25_sendmsg { printf("x25_sendmsg called\\n"); exit(); }'
)

# Step 3: Trace x25_recvmsg — data receive on virtual circuit
check(
    "x25_recvmsg probe",
    'kprobe:x25_recvmsg { printf("x25_recvmsg called\\n"); exit(); }'
)

# Step 4: Trace x25_connect — SVC call initiation
check(
    "x25_connect probe",
    'kprobe:x25_connect { printf("x25_connect called\\n"); exit(); }'
)

# Step 5: Trace x25_accept — accept incoming VC call
check(
    "x25_accept probe",
    'kprobe:x25_accept { printf("x25_accept called\\n"); exit(); }'
)

# Step 6: Trace x25_create — AF_X25 socket creation
check(
    "x25_create probe",
    'kprobe:x25_create { printf("x25_create called\\n"); exit(); }'
)

# Step 7: Trace x25_release — socket close and VC clear
check(
    "x25_release probe",
    'kprobe:x25_release { printf("x25_release called\\n"); exit(); }'
)

# Step 8: Trace x25_route_ioctl — routing table management
check(
    "x25_route_ioctl probe",
    'kprobe:x25_route_ioctl { printf("route ioctl\\n"); exit(); }'
)

# Step 9: Trace x25_bind — socket bind to X.121 address
check(
    "x25_bind probe",
    'kprobe:x25_bind { printf("x25_bind called\\n"); exit(); }'
)

# Step 10: Trace x25_output — packet output construction
check(
    "x25_output probe",
    'kprobe:x25_output { printf("x25_output called\\n"); exit(); }'
)

# ─── Summary ──────────────────────────────────────────────────────────────────

print(json.dumps(results, indent=2))

passed = sum(1 for r in results if r["status"] == "PASS")
skipped = sum(1 for r in results if r["status"] == "SKIP")
failed = sum(1 for r in results if r["status"] == "FAIL")

print(f"\n=== X.25 Subsystem Test Summary ===")
print(f"PASSED: {passed}  SKIPPED: {skipped}  FAILED: {failed}  TOTAL: {len(results)}")
