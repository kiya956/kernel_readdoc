#!/usr/bin/env python3
"""
test_ethernet_subsystem.py — bpftrace-based verification of the Ethernet subsystem.

Steps
-----
1.  Probe eth_type_trans               — protocol type translation
2.  Probe eth_header                   — Ethernet header construction
3.  Probe ether_setup                  — net_device Ethernet defaults
4.  Probe eth_mac_addr                 — MAC address assignment
5.  Probe eth_validate_addr            — MAC address validation
6.  Probe eth_header_parse             — parse Ethernet header
7.  Probe eth_header_cache             — header cache for neighbor
8.  Probe eth_get_headlen              — get header length
9.  Probe eth_gro_receive              — GRO Ethernet receive
10. Ping localhost and verify eth_type_trans — userspace round-trip
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


# ─── helpers ────────────────────────────────────────────────────────────────

def ping_external():
    """Send a single ping to an external host to trigger Ethernet TX/RX."""
    subprocess.run(
        ["ping", "-c1", "-W1", "8.8.8.8"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def arping_trigger():
    """Send an ARP request to the default gateway to exercise eth_header."""
    gw = _default_gateway()
    if gw:
        subprocess.run(
            ["arping", "-c1", "-w1", gw],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    else:
        ping_external()


def create_dummy_iface():
    """Create (and tear down) a dummy interface to trigger ether_setup."""
    subprocess.run(
        ["ip", "link", "add", "dummy0", "type", "dummy"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    time.sleep(0.3)
    subprocess.run(
        ["ip", "link", "del", "dummy0"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def set_dummy_mac():
    """Create a dummy interface and change its MAC to trigger eth_mac_addr."""
    subprocess.run(
        ["ip", "link", "add", "dummy0", "type", "dummy"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    time.sleep(0.2)
    subprocess.run(
        ["ip", "link", "set", "dummy0", "address", "02:00:00:00:00:01"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    time.sleep(0.2)
    subprocess.run(
        ["ip", "link", "del", "dummy0"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def bring_up_dummy():
    """Create and bring up a dummy interface to trigger eth_validate_addr."""
    subprocess.run(
        ["ip", "link", "add", "dummy0", "type", "dummy"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    time.sleep(0.2)
    subprocess.run(
        ["ip", "link", "set", "dummy0", "up"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    time.sleep(0.3)
    subprocess.run(
        ["ip", "link", "del", "dummy0"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _default_gateway() -> str | None:
    """Return the IPv4 default gateway, or None."""
    try:
        out = subprocess.check_output(
            ["ip", "-4", "route", "show", "default"],
            text=True, stderr=subprocess.DEVNULL,
        )
        parts = out.split()
        if "via" in parts:
            return parts[parts.index("via") + 1]
    except Exception:
        pass
    return None


# ─── tests ──────────────────────────────────────────────────────────────────

print("\n=== Ethernet subsystem bpftrace verification ===\n")

# Step 1 — eth_type_trans (protocol demux on receive)
check(1, "eth_type_trans (protocol demux)", r"""
kprobe:eth_type_trans
{
    printf("HIT eth_type_trans\n");
    exit();
}
""", trigger=ping_external)

# Step 2 — eth_header (build Ethernet header on transmit)
check(2, "eth_header (build Ethernet hdr)", r"""
kprobe:eth_header
{
    printf("HIT eth_header\n");
    exit();
}
""", trigger=arping_trigger)

# Step 3 — ether_setup (initialise net_device Ethernet defaults)
check(3, "ether_setup (net_device defaults)", r"""
kprobe:ether_setup
{
    printf("HIT ether_setup\n");
    exit();
}
""", trigger=create_dummy_iface)

# Step 4 — eth_mac_addr (set MAC address)
check(4, "eth_mac_addr (set MAC)", r"""
kprobe:eth_mac_addr
{
    printf("HIT eth_mac_addr\n");
    exit();
}
""", trigger=set_dummy_mac)

# Step 5 — eth_validate_addr (validate MAC address)
check(5, "eth_validate_addr (validate MAC)", r"""
kprobe:eth_validate_addr
{
    printf("HIT eth_validate_addr\n");
    exit();
}
""", trigger=bring_up_dummy)

# Step 6 — eth_header_parse (extract source MAC from header)
check(6, "eth_header_parse (parse src MAC)", r"""
kprobe:eth_header_parse
{
    printf("HIT eth_header_parse\n");
    exit();
}
""", trigger=ping_external)

# Step 7 — eth_header_cache (cache header for neighbour)
check(7, "eth_header_cache (neighbour hdr cache)", r"""
kprobe:eth_header_cache
{
    printf("HIT eth_header_cache\n");
    exit();
}
""", trigger=ping_external)

# Step 8 — eth_get_headlen (flow-dissect header length)
check(8, "eth_get_headlen (get hdr length)", r"""
kprobe:eth_get_headlen
{
    printf("HIT eth_get_headlen\n");
    exit();
}
""", trigger=ping_external)

# Step 9 — eth_gro_receive (GRO for Ethernet-encapsulated frames)
check(9, "eth_gro_receive (GRO receive)", r"""
kprobe:eth_gro_receive
{
    printf("HIT eth_gro_receive\n");
    exit();
}
""", trigger=ping_external)

# Step 10 — Userspace round-trip: ping and confirm eth_type_trans fires
check(10, "Ping + eth_type_trans round-trip", r"""
kprobe:eth_type_trans
{
    @cnt = count();
}

interval:s:3
{
    if (@cnt > 0) {
        printf("HIT eth_type_trans count=%d\n", @cnt);
    }
    exit();
}
""", trigger=ping_external, timeout=15)

# ─── summary ────────────────────────────────────────────────────────────────

print("\n--- Summary ---\n")
passed = sum(1 for _, _, s in results if s == PASS)
failed = sum(1 for _, _, s in results if s == FAIL)
skipped = sum(1 for _, _, s in results if s == SKIP)
total = len(results)

for num, name, status in results:
    print(f"  Step {num:2d}: {name:50s} {status}")

print(f"\n  Total: {total}  Passed: {passed}  Failed: {failed}  Skipped: {skipped}\n")

if failed > 0:
    sys.exit(1)
sys.exit(0)
