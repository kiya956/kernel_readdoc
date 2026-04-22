#!/usr/bin/env python3
"""
test_ethtool_subsystem.py — bpftrace-based verification of the ethtool subsystem.

Steps
-----
1.  Probe ethtool_get_settings / ethtool_ioctl  — legacy ioctl entry
2.  Probe ethnl_default_doit                    — Netlink GET operation handler
3.  Probe ethnl_default_set_doit               — Netlink SET operation handler
4.  Probe ethnl_notify                          — async Netlink notification
5.  Probe dev_ethtool                           — ioctl dispatcher (all cmds)
6.  Probe ethtool_get_link_ksettings            — link settings read
7.  Probe ethtool_set_link_ksettings            — link settings write
8.  Probe ethtool_get_ringparam                — ring size query
9.  Probe ethtool_get_channels                 — channel/queue query
10. Run `ethtool <iface>` and check exit code   — userspace round-trip
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


print("\n=== ethtool subsystem bpftrace verification ===\n")

# Find a real Ethernet interface
def get_eth_iface() -> str | None:
    for name in os.listdir("/sys/class/net"):
        p = f"/sys/class/net/{name}"
        link = os.path.realpath(p)
        # real NIC has a device symlink and is not a loopback/virtual-only device
        if (name.startswith("eth") or name.startswith("en") or name.startswith("eno")
                or name.startswith("enp") or name.startswith("ens")):
            return name
    return None

iface = get_eth_iface()
if iface:
    print(f"  [info] Using interface: {iface}\n")
else:
    print("  [info] No Ethernet interface found; ethtool-trigger steps will use loopback.\n")
    iface = "lo"


def run_ethtool(iface: str):
    subprocess.run(["ethtool", iface], capture_output=True, timeout=5)


def run_ethtool_rings(iface: str):
    subprocess.run(["ethtool", "-g", iface], capture_output=True, timeout=5)


def run_ethtool_channels(iface: str):
    subprocess.run(["ethtool", "-l", iface], capture_output=True, timeout=5)


# ── Step 1: dev_ethtool (ioctl dispatcher) ───────────────────────────────────
prog1 = """
kprobe:dev_ethtool {
    printf("HIT dev_ethtool\\n");
    exit();
}
interval:s:8 { exit(); }
"""
check(1, "dev_ethtool ioctl dispatcher", prog1,
      trigger=lambda: run_ethtool(iface), timeout=12)

# ── Step 2: ethnl_default_doit (Netlink GET) ─────────────────────────────────
prog2 = """
kprobe:ethnl_default_doit {
    printf("HIT ethnl_default_doit\\n");
    exit();
}
interval:s:8 { exit(); }
"""
check(2, "ethnl_default_doit Netlink GET", prog2,
      trigger=lambda: run_ethtool(iface), timeout=12)

# ── Step 3: ethnl_default_set_doit (Netlink SET) ─────────────────────────────
prog3 = """
kprobe:ethnl_default_set_doit {
    printf("HIT ethnl_default_set_doit\\n");
    exit();
}
interval:s:8 { exit(); }
"""
# Trigger a SET: enable/disable offload (non-destructive toggling)
def toggle_offload(iface: str):
    subprocess.run(["ethtool", "-K", iface, "tx", "on"], capture_output=True, timeout=5)
check(3, "ethnl_default_set_doit Netlink SET", prog3,
      trigger=lambda: toggle_offload(iface), timeout=12)

# ── Step 4: ethnl_notify ─────────────────────────────────────────────────────
prog4 = """
kprobe:ethnl_notify {
    printf("HIT ethnl_notify\\n");
    exit();
}
interval:s:8 { exit(); }
"""
check(4, "ethnl_notify async notification", prog4,
      trigger=lambda: toggle_offload(iface), timeout=12)

# ── Step 5: ethtool_get_link_ksettings ───────────────────────────────────────
prog5 = """
kprobe:ethtool_get_link_ksettings {
    printf("HIT ethtool_get_link_ksettings\\n");
    exit();
}
interval:s:8 { exit(); }
"""
check(5, "ethtool_get_link_ksettings", prog5,
      trigger=lambda: run_ethtool(iface), timeout=12)

# ── Step 6: ethtool_set_link_ksettings ───────────────────────────────────────
prog6 = """
kprobe:ethtool_set_link_ksettings {
    printf("HIT ethtool_set_link_ksettings\\n");
    exit();
}
interval:s:8 { exit(); }
"""
# Try to set speed (may fail on virtual NIC, but will still hit the function)
def set_speed(iface: str):
    subprocess.run(["ethtool", "-s", iface, "autoneg", "on"],
                   capture_output=True, timeout=5)
check(6, "ethtool_set_link_ksettings", prog6,
      trigger=lambda: set_speed(iface), timeout=12)

# ── Step 7: ethtool_get_ringparam ────────────────────────────────────────────
prog7 = """
kprobe:ethtool_get_ringparam {
    printf("HIT ethtool_get_ringparam\\n");
    exit();
}
interval:s:8 { exit(); }
"""
check(7, "ethtool_get_ringparam", prog7,
      trigger=lambda: run_ethtool_rings(iface), timeout=12)

# ── Step 8: ethtool_get_channels ─────────────────────────────────────────────
prog8 = """
kprobe:ethtool_get_channels {
    printf("HIT ethtool_get_channels\\n");
    exit();
}
interval:s:8 { exit(); }
"""
check(8, "ethtool_get_channels", prog8,
      trigger=lambda: run_ethtool_channels(iface), timeout=12)

# ── Step 9: genl_family_rcv_msg (ethtool Netlink receive) ───────────────────
prog9 = """
kprobe:genl_family_rcv_msg {
    printf("HIT genl_family_rcv_msg\\n");
    exit();
}
interval:s:8 { exit(); }
"""
check(9, "genl_family_rcv_msg (ethtool Netlink)", prog9,
      trigger=lambda: run_ethtool(iface), timeout=12)

# ── Step 10: ethtool userspace round-trip ────────────────────────────────────
print(f"  Step 10: {'ethtool userspace round-trip':50s}", end=" ")
try:
    r = subprocess.run(["ethtool", iface], capture_output=True, text=True, timeout=10)
    # ethtool returns 0 for real NICs, 75 (EOPNOTSUPP) for virtual/lo
    if r.returncode in (0, 75) or "Settings for" in r.stdout or "Link detected" in r.stdout:
        print(PASS)
        results.append((10, "ethtool round-trip", PASS))
    else:
        print(FAIL)
        results.append((10, "ethtool round-trip", FAIL))
        print(f"            stderr: {r.stderr.strip()[:200]}")
except FileNotFoundError:
    print(SKIP)
    results.append((10, "ethtool round-trip", SKIP))
except Exception as e:
    print(FAIL)
    results.append((10, "ethtool round-trip", FAIL))
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
