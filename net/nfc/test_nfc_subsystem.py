#!/usr/bin/env python3
"""
test_nfc_subsystem.py — bpftrace-based verification of the NFC subsystem.

Steps
-----
1.  Probe nfc_register_device          — NFC device registration
2.  Probe nfc_unregister_device        — NFC device unregistration
3.  Probe nfc_alloc_recv_skb           — NFC receive buffer allocation
4.  Probe nci_register_device          — NCI device registration
5.  Probe nci_send_cmd                 — NCI command send
6.  Probe nfc_llcp_send_ui_frame       — LLCP UI frame transmit
7.  Probe nfc_genl_dev_up              — netlink device activation
8.  Probe nfc_genl_dev_down            — netlink device deactivation
9.  Probe nfc_start_poll               — NFC polling start
10. Check NFC module loaded            — lsmod/modinfo round-trip
"""

import subprocess
import sys
import os
import time
import tempfile

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
SKIP = "\033[33mSKIP\033[0m"

BPFTRACE = "/usr/bin/bpftrace"
ATTACH_WAIT = 2.0


def run_bpftrace(program: str, trigger=None, timeout: int = 10) -> tuple[str, str, bool]:
    with tempfile.NamedTemporaryFile("w", suffix=".bt", delete=False) as f:
        f.write(program)
        bt_file = f.name
    try:
        proc = subprocess.Popen(
            [BPFTRACE, bt_file],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        time.sleep(ATTACH_WAIT)
        if trigger:
            try:
                trigger()
            except Exception:
                pass
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
        skipped = any(
            kw in stderr for kw in ("not traceable", "No probes", "unrecognized")
        )
        return stdout, stderr, skipped
    finally:
        os.unlink(bt_file)


results = []


def check(step_num: int, name: str, program: str, trigger=None,
          expect: str = "HIT", timeout: int = 12):
    stdout, stderr, skipped = run_bpftrace(program, trigger, timeout)
    if skipped:
        results.append((step_num, name, SKIP))
        print(f"  Step {step_num:2d}: {name:50s} {SKIP}")
        return
    ok = expect in stdout
    status = PASS if ok else FAIL
    results.append((step_num, name, status))
    print(f"  Step {step_num:2d}: {name:50s} {status}")
    if not ok:
        if stdout.strip():
            print(f"            stdout: {stdout.strip()[:200]}")
        if stderr.strip():
            print(f"            stderr: {stderr.strip()[:200]}")


# ---------------------------------------------------------------------------
# Helper: attempt to load the NFC module so kprobes can attach
# ---------------------------------------------------------------------------

def _try_load_nfc():
    """Best-effort modprobe nfc; ignored if module is built-in or unavailable."""
    subprocess.run(["modprobe", "nfc"], capture_output=True)


def _trigger_nfc_modprobe():
    """Trigger that loads the nfc module — may cause nfc_register_device to fire."""
    subprocess.run(["modprobe", "-r", "nfc"], capture_output=True)
    time.sleep(0.3)
    subprocess.run(["modprobe", "nfc"], capture_output=True)


print("\n=== NFC subsystem bpftrace verification ===\n")

# Pre-flight: try to ensure the nfc module is available
_try_load_nfc()

# ── Step 1: nfc_register_device ──────────────────────────────────────────────
check(1, "nfc_register_device probe",
      r"""
kprobe:nfc_register_device
{
    printf("HIT nfc_register_device comm=%s\n", comm);
    exit();
}

interval:s:3 { exit(); }
""",
      trigger=_trigger_nfc_modprobe)

# ── Step 2: nfc_unregister_device ────────────────────────────────────────────
check(2, "nfc_unregister_device probe",
      r"""
kprobe:nfc_unregister_device
{
    printf("HIT nfc_unregister_device comm=%s\n", comm);
    exit();
}

interval:s:3 { exit(); }
""",
      trigger=lambda: subprocess.run(["modprobe", "-r", "nfc"],
                                     capture_output=True))

# ── Step 3: nfc_alloc_recv_skb ───────────────────────────────────────────────
check(3, "nfc_alloc_recv_skb probe",
      r"""
kprobe:nfc_alloc_recv_skb
{
    printf("HIT nfc_alloc_recv_skb comm=%s\n", comm);
    exit();
}

interval:s:3 { exit(); }
""")

# ── Step 4: nci_register_device ──────────────────────────────────────────────
check(4, "nci_register_device probe",
      r"""
kprobe:nci_register_device
{
    printf("HIT nci_register_device comm=%s\n", comm);
    exit();
}

interval:s:3 { exit(); }
""",
      trigger=lambda: subprocess.run(["modprobe", "nci"],
                                     capture_output=True))

# ── Step 5: nci_send_cmd ─────────────────────────────────────────────────────
check(5, "nci_send_cmd probe",
      r"""
kprobe:nci_send_cmd
{
    printf("HIT nci_send_cmd comm=%s\n", comm);
    exit();
}

interval:s:3 { exit(); }
""")

# ── Step 6: nfc_llcp_send_ui_frame ───────────────────────────────────────────
check(6, "nfc_llcp_send_ui_frame probe",
      r"""
kprobe:nfc_llcp_send_ui_frame
{
    printf("HIT nfc_llcp_send_ui_frame comm=%s\n", comm);
    exit();
}

interval:s:3 { exit(); }
""",
      trigger=lambda: subprocess.run(["modprobe", "nfc_llcp"],
                                     capture_output=True))

# ── Step 7: nfc_genl_dev_up ──────────────────────────────────────────────────
check(7, "nfc_genl_dev_up probe",
      r"""
kprobe:nfc_genl_dev_up
{
    printf("HIT nfc_genl_dev_up comm=%s\n", comm);
    exit();
}

interval:s:3 { exit(); }
""")

# ── Step 8: nfc_genl_dev_down ────────────────────────────────────────────────
check(8, "nfc_genl_dev_down probe",
      r"""
kprobe:nfc_genl_dev_down
{
    printf("HIT nfc_genl_dev_down comm=%s\n", comm);
    exit();
}

interval:s:3 { exit(); }
""")

# ── Step 9: nfc_start_poll ───────────────────────────────────────────────────
check(9, "nfc_start_poll probe",
      r"""
kprobe:nfc_start_poll
{
    printf("HIT nfc_start_poll comm=%s\n", comm);
    exit();
}

interval:s:3 { exit(); }
""")

# ── Step 10: Check NFC module loaded ─────────────────────────────────────────
print()  # visual separator before non-bpftrace step


def step10_check_nfc_module():
    """Verify the NFC module is available via modinfo or currently loaded."""
    # First try modinfo — works even if the module is not loaded
    ret = subprocess.run(["modinfo", "nfc"], capture_output=True, text=True)
    if ret.returncode == 0 and "filename:" in ret.stdout:
        return True, "modinfo reports nfc module available"

    # Fall back to lsmod
    ret = subprocess.run(["lsmod"], capture_output=True, text=True)
    if ret.returncode == 0:
        for line in ret.stdout.splitlines():
            if line.startswith("nfc ") or line.startswith("nfc\t"):
                return True, "nfc module currently loaded"

    # Check if built-in
    uname = subprocess.run(["uname", "-r"], capture_output=True, text=True)
    builtin_path = f"/lib/modules/{uname.stdout.strip()}/modules.builtin"
    if os.path.exists(builtin_path):
        with open(builtin_path) as f:
            for line in f:
                if "nfc/nfc.ko" in line:
                    return True, "nfc is built-in"

    return False, "nfc module not found"


ok, detail = step10_check_nfc_module()
status = PASS if ok else FAIL
results.append((10, "NFC module available", status))
print(f"  Step 10: {'NFC module available':50s} {status}")
if not ok:
    print(f"            detail: {detail}")

# ── Summary ──────────────────────────────────────────────────────────────────
print("\n" + "=" * 62)
print("  Summary")
print("=" * 62)
passed = sum(1 for _, _, s in results if s == PASS)
failed = sum(1 for _, _, s in results if s == FAIL)
skipped = sum(1 for _, _, s in results if s == SKIP)
total = len(results)
print(f"  Total: {total}   Passed: {passed}   Failed: {failed}   Skipped: {skipped}")
print("=" * 62 + "\n")

sys.exit(1 if failed else 0)
