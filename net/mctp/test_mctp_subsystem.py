#!/usr/bin/env python3
"""
test_mctp_subsystem.py — bpftrace verification of the MCTP subsystem.

Steps
-----
1.  Check AF_MCTP socket creation
2.  Probe mctp_sendmsg              — outgoing MCTP message
3.  Probe mctp_recvmsg              — incoming MCTP message
4.  Probe mctp_route_output         — routing / fragment output
5.  Probe mctp_route_rcv            — incoming frame routing
6.  Probe mctp_sk_expire_keys       — tag expiry timer
7.  Probe mctp_neigh_lookup         — neighbour table lookup
8.  Probe mctp_dev_get_rtnl         — MCTP device lookup
9.  Check /proc/net/mctp_dev         — procfs presence
10. Check CONFIG_MCTP in kernel config
"""

import subprocess
import sys
import os
import time
import tempfile
import socket

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
SKIP = "\033[33mSKIP\033[0m"

BPFTRACE = "/usr/bin/bpftrace"
ATTACH_WAIT = 2.0
AF_MCTP = 45  # from include/uapi/linux/socket.h


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
          expect: str = "HIT", timeout: int = 10):
    stdout, stderr, skipped = run_bpftrace(program, trigger, timeout)
    if skipped:
        results.append((step_num, name, SKIP))
        print(f"  Step {step_num:2d}: {name:48s} {SKIP}")
        return
    ok = expect in stdout
    status = PASS if ok else FAIL
    results.append((step_num, name, status))
    print(f"  Step {step_num:2d}: {name:48s} {status}")
    if not ok:
        if stdout.strip():
            print(f"            stdout: {stdout.strip()[:200]}")
        if stderr.strip():
            print(f"            stderr: {stderr.strip()[:200]}")


print("\n=== MCTP subsystem bpftrace verification ===\n")

# ── Step 1: AF_MCTP socket creation ──────────────────────────────────────────
print(f"  Step  1: {'AF_MCTP socket creation':48s}", end=" ")
mctp_available = False
try:
    s = socket.socket(AF_MCTP, socket.SOCK_DGRAM, 0)
    s.close()
    mctp_available = True
    print(PASS)
    results.append((1, "AF_MCTP socket", PASS))
except OSError as e:
    if e.errno in (97, 22):  # EAFNOSUPPORT or EINVAL
        print(SKIP)
        results.append((1, "AF_MCTP socket", SKIP))
    else:
        print(FAIL)
        results.append((1, "AF_MCTP socket", FAIL))


def mctp_socket_trigger():
    """Create an AF_MCTP socket to trigger socket-related probes."""
    try:
        s = socket.socket(AF_MCTP, socket.SOCK_DGRAM, 0)
        s.close()
    except Exception:
        pass


# ── Step 2: mctp_sendmsg ─────────────────────────────────────────────────────
prog2 = """
kprobe:mctp_sendmsg {
    printf("HIT mctp_sendmsg\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(2, "mctp_sendmsg kprobe", prog2, timeout=8)

# ── Step 3: mctp_recvmsg ─────────────────────────────────────────────────────
prog3 = """
kprobe:mctp_recvmsg {
    printf("HIT mctp_recvmsg\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(3, "mctp_recvmsg kprobe", prog3, timeout=8)

# ── Step 4: mctp_route_output ────────────────────────────────────────────────
prog4 = """
kprobe:mctp_route_output {
    printf("HIT mctp_route_output\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(4, "mctp_route_output kprobe", prog4, timeout=8)

# ── Step 5: mctp_route_rcv ───────────────────────────────────────────────────
prog5 = """
kprobe:mctp_route_rcv {
    printf("HIT mctp_route_rcv\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(5, "mctp_route_rcv kprobe", prog5, timeout=8)

# ── Step 6: mctp_sk_expire_keys ──────────────────────────────────────────────
prog6 = """
kprobe:mctp_sk_expire_keys {
    printf("HIT mctp_sk_expire_keys\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(6, "mctp_sk_expire_keys kprobe", prog6,
      trigger=mctp_socket_trigger, timeout=8)

# ── Step 7: mctp_neigh_lookup ────────────────────────────────────────────────
prog7 = """
kprobe:mctp_neigh_lookup {
    printf("HIT mctp_neigh_lookup\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(7, "mctp_neigh_lookup kprobe", prog7, timeout=8)

# ── Step 8: mctp_dev_get_rtnl / mctp_dev_find_by_index ──────────────────────
prog8 = """
kprobe:mctp_dev_get_rtnl,
kprobe:mctp_dev_find_by_index {
    printf("HIT mctp_dev_get\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(8, "mctp_dev_get_rtnl/find_by_index kprobe", prog8, timeout=8)

# ── Step 9: /proc/net/mctp_dev ───────────────────────────────────────────────
print(f"  Step  9: {'/proc/net/mctp_dev exists':48s}", end=" ")
if os.path.exists("/proc/net/mctp_dev"):
    print(PASS)
    results.append((9, "procfs mctp_dev", PASS))
else:
    print(SKIP)
    results.append((9, "procfs mctp_dev", SKIP))

# ── Step 10: CONFIG_MCTP ─────────────────────────────────────────────────────
print(f"  Step 10: {'CONFIG_MCTP in kernel config':48s}", end=" ")
mctp_configured = False
config_files = ["/proc/config.gz", "/boot/config-" + os.uname().release]
for cf in config_files:
    if not os.path.exists(cf):
        continue
    try:
        if cf.endswith(".gz"):
            import gzip
            data = gzip.open(cf, "rt").read()
        else:
            data = open(cf).read()
        if "CONFIG_MCTP=y" in data or "CONFIG_MCTP=m" in data:
            mctp_configured = True
            break
    except Exception:
        pass
if mctp_configured:
    print(PASS)
    results.append((10, "CONFIG_MCTP", PASS))
else:
    print(SKIP)
    results.append((10, "CONFIG_MCTP", SKIP))

# ── Summary ──────────────────────────────────────────────────────────────────
print()
passed = sum(1 for _, _, s in results if s == PASS)
failed = sum(1 for _, _, s in results if s == FAIL)
skipped = sum(1 for _, _, s in results if s == SKIP)
total = len(results)
print(f"Results: {passed}/{total} passed, {failed} failed, {skipped} skipped")
if failed > 0:
    sys.exit(1)
