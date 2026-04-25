#!/usr/bin/env python3
"""
test_wireless_subsystem.py — bpftrace-based verification of the cfg80211 wireless subsystem.

Steps
-----
1.  Probe cfg80211_scan_done            — scan completion notification
2.  Probe cfg80211_connect_result       — connection result reporting
3.  Probe wiphy_register                — wiphy device registration
4.  Probe cfg80211_rx_mgmt              — management frame reception
5.  Probe regulatory_hint               — regulatory domain hinting
6.  Probe cfg80211_put_bss              — BSS reference release
7.  Probe nl80211_send_scan_msg         — scan result Netlink notification
8.  Probe wiphy_new_nm                  — wiphy allocation
9.  Probe cfg80211_get_drvinfo          — ethtool driver info
10. Check /sys/class/ieee80211           — sysfs wiphy presence
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


print("\n=== cfg80211 wireless subsystem bpftrace verification ===\n")


# Helper: find a wireless interface
def get_wlan_iface() -> str | None:
    ieee_path = "/sys/class/ieee80211"
    if not os.path.isdir(ieee_path):
        return None
    for phy in os.listdir(ieee_path):
        net_dir = f"{ieee_path}/{phy}/device/net"
        if os.path.isdir(net_dir):
            ifaces = os.listdir(net_dir)
            if ifaces:
                return ifaces[0]
    # Fallback: look for wlan* in /sys/class/net
    for name in os.listdir("/sys/class/net"):
        if name.startswith("wlan") or name.startswith("wlp"):
            return name
    return None


wlan = get_wlan_iface()
if wlan:
    print(f"  [info] Using wireless interface: {wlan}\n")
else:
    print("  [info] No wireless interface found; some probes will likely SKIP.\n")


def trigger_scan():
    """Trigger a WiFi scan via iw."""
    if wlan:
        subprocess.run(["iw", "dev", wlan, "scan", "trigger"],
                       capture_output=True, timeout=5)
        time.sleep(2)
        subprocess.run(["iw", "dev", wlan, "scan", "dump"],
                       capture_output=True, timeout=5)


def trigger_ethtool():
    """Trigger ethtool on wireless interface."""
    if wlan:
        subprocess.run(["ethtool", "-i", wlan], capture_output=True, timeout=5)


# ── Step 1: cfg80211_scan_done ─────────────────────────────────────────────
prog1 = """
kprobe:cfg80211_scan_done {
    printf("HIT cfg80211_scan_done\\n");
    exit();
}
interval:s:8 { exit(); }
"""
check(1, "cfg80211_scan_done scan completion", prog1,
      trigger=trigger_scan, timeout=12)

# ── Step 2: cfg80211_connect_result ────────────────────────────────────────
prog2 = """
kprobe:cfg80211_connect_result {
    printf("HIT cfg80211_connect_result\\n");
    exit();
}
interval:s:8 { exit(); }
"""
check(2, "cfg80211_connect_result connection report", prog2, timeout=12)

# ── Step 3: wiphy_register ─────────────────────────────────────────────────
prog3 = """
kprobe:wiphy_register {
    printf("HIT wiphy_register\\n");
    exit();
}
interval:s:8 { exit(); }
"""
check(3, "wiphy_register device registration", prog3, timeout=12)

# ── Step 4: cfg80211_rx_mgmt ──────────────────────────────────────────────
prog4 = """
kprobe:cfg80211_rx_mgmt {
    printf("HIT cfg80211_rx_mgmt\\n");
    exit();
}
interval:s:8 { exit(); }
"""
check(4, "cfg80211_rx_mgmt management frame rx", prog4,
      trigger=trigger_scan, timeout=12)

# ── Step 5: regulatory_hint ───────────────────────────────────────────────
prog5 = """
kprobe:regulatory_hint {
    printf("HIT regulatory_hint\\n");
    exit();
}
interval:s:8 { exit(); }
"""
check(5, "regulatory_hint regulatory domain", prog5, timeout=12)

# ── Step 6: cfg80211_put_bss ──────────────────────────────────────────────
prog6 = """
kprobe:cfg80211_put_bss {
    printf("HIT cfg80211_put_bss\\n");
    exit();
}
interval:s:8 { exit(); }
"""
check(6, "cfg80211_put_bss BSS reference release", prog6,
      trigger=trigger_scan, timeout=12)

# ── Step 7: nl80211_send_scan_msg ─────────────────────────────────────────
prog7 = """
kprobe:nl80211_send_scan_msg {
    printf("HIT nl80211_send_scan_msg\\n");
    exit();
}
interval:s:8 { exit(); }
"""
check(7, "nl80211_send_scan_msg scan notification", prog7,
      trigger=trigger_scan, timeout=12)

# ── Step 8: wiphy_new_nm ─────────────────────────────────────────────────
prog8 = """
kprobe:wiphy_new_nm {
    printf("HIT wiphy_new_nm\\n");
    exit();
}
interval:s:8 { exit(); }
"""
check(8, "wiphy_new_nm wiphy allocation", prog8, timeout=12)

# ── Step 9: cfg80211_get_drvinfo ──────────────────────────────────────────
prog9 = """
kprobe:cfg80211_get_drvinfo {
    printf("HIT cfg80211_get_drvinfo\\n");
    exit();
}
interval:s:8 { exit(); }
"""
check(9, "cfg80211_get_drvinfo ethtool driver info", prog9,
      trigger=trigger_ethtool, timeout=12)

# ── Step 10: /sys/class/ieee80211 sysfs check ────────────────────────────
print(f"  Step 10: {'sysfs /sys/class/ieee80211 presence':50s}", end=" ")
try:
    ieee_path = "/sys/class/ieee80211"
    if os.path.isdir(ieee_path):
        phys = os.listdir(ieee_path)
        if phys:
            print(PASS)
            results.append((10, "sysfs ieee80211 presence", PASS))
            print(f"            wiphy devices: {', '.join(phys)}")
        else:
            print(SKIP)
            results.append((10, "sysfs ieee80211 presence", SKIP))
            print("            (no wiphy devices found)")
    else:
        print(SKIP)
        results.append((10, "sysfs ieee80211 presence", SKIP))
        print("            (/sys/class/ieee80211 not present)")
except Exception as e:
    print(FAIL)
    results.append((10, "sysfs ieee80211 presence", FAIL))
    print(f"            error: {e}")

# ── Summary ──────────────────────────────────────────────────────────────────
print()
passed = sum(1 for _, _, s in results if s == PASS)
failed = sum(1 for _, _, s in results if s == FAIL)
skipped = sum(1 for _, _, s in results if s == SKIP)
total = len(results)
print(f"Results: {passed}/{total} passed, {failed} failed, {skipped} skipped")
if failed > 0:
    sys.exit(1)
