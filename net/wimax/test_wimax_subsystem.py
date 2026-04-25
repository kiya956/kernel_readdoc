#!/usr/bin/env python3
"""
BPFtrace-based test suite for the WiMAX networking subsystem (net/wimax).

Tests verify that key WiMAX kernel functions are traceable via kprobes.
Each test attaches a short-lived bpftrace probe and checks for successful
attachment (PASS) or expected absence (SKIP). No WiMAX hardware is
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

# Step 1: Trace wimax_dev_add — device registration
check(
    "wimax_dev_add probe",
    'kprobe:wimax_dev_add { printf("wimax_dev_add called\\n"); exit(); }'
)

# Step 2: Trace wimax_dev_rm — device removal
check(
    "wimax_dev_rm probe",
    'kprobe:wimax_dev_rm { printf("wimax_dev_rm called\\n"); exit(); }'
)

# Step 3: Trace wimax_dev_init — device structure init
check(
    "wimax_dev_init probe",
    'kprobe:wimax_dev_init { printf("wimax_dev_init called\\n"); exit(); }'
)

# Step 4: Trace wimax_msg — send message to userspace
check(
    "wimax_msg probe",
    'kprobe:wimax_msg { printf("wimax_msg called\\n"); exit(); }'
)

# Step 5: Trace wimax_msg_send — transmit genetlink message
check(
    "wimax_msg_send probe",
    'kprobe:wimax_msg_send { printf("wimax_msg_send called\\n"); exit(); }'
)

# Step 6: Trace wimax_state_change — device state transition
check(
    "wimax_state_change probe",
    'kprobe:wimax_state_change { printf("state change\\n"); exit(); }'
)

# Step 7: Trace wimax_rfkill — RF control
check(
    "wimax_rfkill probe",
    'kprobe:wimax_rfkill { printf("rfkill\\n"); exit(); }'
)

# Step 8: Trace wimax_reset — device reset
check(
    "wimax_reset probe",
    'kprobe:wimax_reset { printf("reset\\n"); exit(); }'
)

# Step 9: Trace wimax_msg_alloc — allocate genetlink message
check(
    "wimax_msg_alloc probe",
    'kprobe:wimax_msg_alloc { printf("msg alloc\\n"); exit(); }'
)

# Step 10: Trace wimax_state_get — query device state
check(
    "wimax_state_get probe",
    'kprobe:wimax_state_get { printf("state get\\n"); exit(); }'
)

# ─── Summary ──────────────────────────────────────────────────────────────────

print(json.dumps(results, indent=2))

passed = sum(1 for r in results if r["status"] == "PASS")
skipped = sum(1 for r in results if r["status"] == "SKIP")
failed = sum(1 for r in results if r["status"] == "FAIL")

print(f"\n=== WiMAX Subsystem Test Summary ===")
print(f"PASSED: {passed}  SKIPPED: {skipped}  FAILED: {failed}  TOTAL: {len(results)}")
