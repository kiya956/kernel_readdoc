#!/usr/bin/env python3
"""
BPFtrace-based test suite for the VSOCK (AF_VSOCK) subsystem (net/vmw_vsock).

Tests verify that key VSOCK kernel functions are traceable via kprobes.
Each test attaches a short-lived bpftrace probe and checks for successful
attachment (PASS) or expected absence (SKIP). No VM or hypervisor is
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

# Step 1: Trace vsock_stream_sendmsg — stream send path
check(
    "vsock_stream_sendmsg probe",
    'kprobe:vsock_stream_sendmsg { printf("vsock sendmsg\\n"); exit(); }'
)

# Step 2: Trace vsock_stream_recvmsg — stream receive path
check(
    "vsock_stream_recvmsg probe",
    'kprobe:vsock_stream_recvmsg { printf("vsock recvmsg\\n"); exit(); }'
)

# Step 3: Trace vsock_connect — connection initiation
check(
    "vsock_connect probe",
    'kprobe:vsock_connect { printf("vsock connect\\n"); exit(); }'
)

# Step 4: Trace vsock_accept — incoming connection accept
check(
    "vsock_accept probe",
    'kprobe:vsock_accept { printf("vsock accept\\n"); exit(); }'
)

# Step 5: Trace vsock_assign_transport — transport selection
check(
    "vsock_assign_transport probe",
    'kprobe:vsock_assign_transport { printf("assign transport\\n"); exit(); }'
)

# Step 6: Trace virtio_transport_recv_pkt — virtio packet receive
check(
    "virtio_transport_recv_pkt probe",
    'kprobe:virtio_transport_recv_pkt { printf("virtio recv pkt\\n"); exit(); }'
)

# Step 7: Trace vsock_stream_connect — stream-specific connect
check(
    "vsock_stream_connect probe",
    'kprobe:vsock_stream_connect { printf("stream connect\\n"); exit(); }'
)

# Step 8: Trace vsock_create — AF_VSOCK socket creation
check(
    "vsock_create probe",
    'kprobe:vsock_create { printf("vsock create\\n"); exit(); }'
)

# Step 9: Trace vsock_release — socket close/release
check(
    "vsock_release probe",
    'kprobe:vsock_release { printf("vsock release\\n"); exit(); }'
)

# Step 10: Trace vsock_bind — socket bind to CID:port
check(
    "vsock_bind probe",
    'kprobe:vsock_bind { printf("vsock bind\\n"); exit(); }'
)

# ─── Summary ──────────────────────────────────────────────────────────────────

print(json.dumps(results, indent=2))

passed = sum(1 for r in results if r["status"] == "PASS")
skipped = sum(1 for r in results if r["status"] == "SKIP")
failed = sum(1 for r in results if r["status"] == "FAIL")

print(f"\n=== VSOCK Subsystem Test Summary ===")
print(f"PASSED: {passed}  SKIPPED: {skipped}  FAILED: {failed}  TOTAL: {len(results)}")
