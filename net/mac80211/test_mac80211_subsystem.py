#!/usr/bin/env python3
"""
test_mac80211_subsystem.py — bpftrace-based verification of the mac80211 subsystem.

Steps
-----
1.  Probe ieee80211_register_hw        — WiFi driver registration
2.  Probe ieee80211_rx                 — frame receive entry
3.  Probe ieee80211_rx_napi            — NAPI receive path
4.  Probe ieee80211_subif_start_xmit   — TX from network stack
5.  Probe ieee80211_tx                 — internal TX processing
6.  Probe ieee80211_sta_rx_queued_mgmt — management frame processing
7.  Probe ieee80211_key_alloc          — encryption key allocation
8.  Probe rate_control_get_rate        — TX rate selection
9.  Probe ieee80211_agg_start_txq      — A-MPDU aggregation start
10. Check mac80211 module loaded        — lsmod/modinfo round-trip
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
# Triggers
# ---------------------------------------------------------------------------

def trigger_iw_dev():
    """List WiFi devices — lightweight, always safe."""
    subprocess.run(["iw", "dev"], capture_output=True, timeout=5)


def trigger_iw_phy():
    """List WiFi phys — reads hardware capabilities."""
    subprocess.run(["iw", "phy"], capture_output=True, timeout=5)


def trigger_wifi_scan():
    """Attempt a WiFi scan — may fail if no wlan0 or no permissions."""
    subprocess.run(
        ["iw", "dev", "wlan0", "scan", "trigger"],
        capture_output=True, timeout=5,
    )
    time.sleep(1)


def trigger_wifi_link():
    """Query link status — touches RX/TX stats path."""
    subprocess.run(
        ["iw", "dev", "wlan0", "link"],
        capture_output=True, timeout=5,
    )


# ---------------------------------------------------------------------------
# Steps 1–9: bpftrace kprobes
# ---------------------------------------------------------------------------

print("\n=== mac80211 subsystem bpftrace verification ===\n")

# Step 1 — ieee80211_register_hw (fires at driver load time, unlikely during
# test; attach briefly and report if symbol is traceable)
check(1, "ieee80211_register_hw (probe attach)", """
kprobe:ieee80211_register_hw {
    printf("HIT register_hw\\n");
    exit();
}
interval:ms:500 { exit(); }
""", trigger=trigger_iw_dev, timeout=8)

# Step 2 — ieee80211_rx (main RX entry; fires on every received frame)
check(2, "ieee80211_rx (frame receive)", """
kprobe:ieee80211_rx {
    printf("HIT rx\\n");
    exit();
}
interval:ms:2000 { exit(); }
""", trigger=trigger_wifi_link, timeout=10)

# Step 3 — ieee80211_rx_napi (NAPI RX variant)
check(3, "ieee80211_rx_napi (NAPI receive path)", """
kprobe:ieee80211_rx_napi {
    printf("HIT rx_napi\\n");
    exit();
}
interval:ms:2000 { exit(); }
""", trigger=trigger_wifi_link, timeout=10)

# Step 4 — ieee80211_subif_start_xmit (TX entry from network stack)
check(4, "ieee80211_subif_start_xmit (TX start)", """
kprobe:ieee80211_subif_start_xmit {
    printf("HIT subif_start_xmit\\n");
    exit();
}
interval:ms:2000 { exit(); }
""", trigger=trigger_wifi_link, timeout=10)

# Step 5 — ieee80211_tx (internal TX processing)
check(5, "ieee80211_tx (internal TX)", """
kprobe:ieee80211_tx {
    printf("HIT tx\\n");
    exit();
}
interval:ms:2000 { exit(); }
""", trigger=trigger_wifi_scan, timeout=10)

# Step 6 — ieee80211_sta_rx_queued_mgmt (management frame handling)
check(6, "ieee80211_sta_rx_queued_mgmt (mgmt frames)", """
kprobe:ieee80211_sta_rx_queued_mgmt {
    printf("HIT sta_rx_queued_mgmt\\n");
    exit();
}
interval:ms:2000 { exit(); }
""", trigger=trigger_wifi_scan, timeout=10)

# Step 7 — ieee80211_key_alloc (encryption key allocation)
check(7, "ieee80211_key_alloc (key allocation)", """
kprobe:ieee80211_key_alloc {
    printf("HIT key_alloc\\n");
    exit();
}
interval:ms:500 { exit(); }
""", trigger=trigger_iw_dev, timeout=8)

# Step 8 — rate_control_get_rate (TX rate selection)
check(8, "rate_control_get_rate (rate selection)", """
kprobe:rate_control_get_rate {
    printf("HIT rate_control_get_rate\\n");
    exit();
}
interval:ms:2000 { exit(); }
""", trigger=trigger_wifi_link, timeout=10)

# Step 9 — ieee80211_agg_start_txq (A-MPDU aggregation)
check(9, "ieee80211_agg_start_txq (A-MPDU agg)", """
kprobe:ieee80211_agg_start_txq {
    printf("HIT agg_start_txq\\n");
    exit();
}
interval:ms:500 { exit(); }
""", trigger=trigger_iw_dev, timeout=8)

# ---------------------------------------------------------------------------
# Step 10: Module loaded check (no bpftrace needed)
# ---------------------------------------------------------------------------

def step10_module_check():
    """Verify mac80211 module is loaded or available."""
    # Try lsmod first
    lsmod = subprocess.run(
        ["lsmod"], capture_output=True, text=True, timeout=5,
    )
    if "mac80211" in lsmod.stdout:
        results.append((10, "mac80211 module loaded (lsmod)", PASS))
        print(f"  Step 10: {'mac80211 module loaded (lsmod)':50s} {PASS}")
        return

    # Try modinfo as fallback — module exists but may not be loaded
    modinfo = subprocess.run(
        ["modinfo", "mac80211"], capture_output=True, text=True, timeout=5,
    )
    if modinfo.returncode == 0:
        results.append((10, "mac80211 module available (modinfo)", PASS))
        print(f"  Step 10: {'mac80211 module available (modinfo)':50s} {PASS}")
        return

    results.append((10, "mac80211 module not found", FAIL))
    print(f"  Step 10: {'mac80211 module not found':50s} {FAIL}")


step10_module_check()

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

print("\n--- Summary ---\n")
passed = sum(1 for _, _, s in results if s == PASS)
failed = sum(1 for _, _, s in results if s == FAIL)
skipped = sum(1 for _, _, s in results if s == SKIP)
total = len(results)

print(f"  Total : {total}")
print(f"  Passed: {passed}")
print(f"  Failed: {failed}")
print(f"  Skipped: {skipped}")

if failed:
    print(f"\n  Result: {FAIL}")
    sys.exit(1)
else:
    print(f"\n  Result: {PASS} (all passed or skipped)")
    sys.exit(0)
