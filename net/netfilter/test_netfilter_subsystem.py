#!/usr/bin/env python3
"""
test_netfilter_subsystem.py — bpftrace verification of net/netfilter packet
filtering, connection tracking, NAT, and nftables/iptables evaluation.

Steps
-----
1.  Probe nf_hook_slow          — netfilter hook execution
2.  Probe nf_conntrack_in       — connection tracking input
3.  Probe nf_nat_manip_pkt      — NAT packet manipulation
4.  Probe nft_do_chain          — nftables chain evaluation
5.  Probe __nf_ct_refresh_acct  — conntrack refresh
6.  Probe nf_ct_delete          — conntrack delete
7.  Probe ipt_do_table          — iptables table evaluation
8.  Probe nf_log_packet         — netfilter logging
9.  Probe nf_conntrack_find_get — conntrack lookup
10. Check /proc/net/nf_conntrack readable
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


def run_bpftrace(program: str, trigger=None, timeout: int = 10) -> tuple:
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
        print(f"  Step {step_num:2d}: {name:52s} {SKIP}")
        return
    ok = expect in stdout
    status = PASS if ok else FAIL
    results.append((step_num, name, status))
    print(f"  Step {step_num:2d}: {name:52s} {status}")
    if not ok:
        if stdout.strip():
            print(f"            stdout: {stdout.strip()[:200]}")


def trigger_udp_loopback():
    """Send a UDP datagram to loopback to exercise netfilter hooks."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.sendto(b"netfilter-probe-test", ("127.0.0.1", 9))
        s.close()
    except Exception:
        pass


def trigger_udp_burst():
    """Send several UDP datagrams to increase the chance of hitting probes."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        for _ in range(5):
            s.sendto(b"netfilter-burst", ("127.0.0.1", 9))
        s.close()
    except Exception:
        pass


def trigger_tcp_loopback():
    """Attempt a TCP connection to loopback to create a conntrack entry."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1)
        s.connect(("127.0.0.1", 22))
        s.close()
    except Exception:
        pass


def main():
    if os.geteuid() != 0:
        print("ERROR: must be run as root (bpftrace requires CAP_BPF)")
        sys.exit(1)

    print("\n=== net/netfilter subsystem — bpftrace verification ===\n")

    # Step 1: nf_hook_slow — netfilter hook execution
    check(1, "nf_hook_slow (hook execution)",
          'kprobe:nf_hook_slow { printf("HIT\\n"); exit(); }',
          trigger=trigger_udp_loopback)

    # Step 2: nf_conntrack_in — connection tracking input
    check(2, "nf_conntrack_in (conntrack input)",
          'kprobe:nf_conntrack_in { printf("HIT\\n"); exit(); }',
          trigger=trigger_udp_loopback)

    # Step 3: nf_nat_manip_pkt — NAT packet manipulation
    check(3, "nf_nat_manip_pkt (NAT manipulation)",
          'kprobe:nf_nat_manip_pkt { printf("HIT\\n"); exit(); }',
          trigger=trigger_udp_loopback)

    # Step 4: nft_do_chain — nftables chain evaluation
    check(4, "nft_do_chain (nftables chain eval)",
          'kprobe:nft_do_chain { printf("HIT\\n"); exit(); }',
          trigger=trigger_udp_loopback)

    # Step 5: __nf_ct_refresh_acct — conntrack refresh
    check(5, "__nf_ct_refresh_acct (conntrack refresh)",
          'kprobe:__nf_ct_refresh_acct { printf("HIT\\n"); exit(); }',
          trigger=trigger_udp_burst)

    # Step 6: nf_ct_delete — conntrack delete
    check(6, "nf_ct_delete (conntrack delete)",
          'kprobe:nf_ct_delete { printf("HIT\\n"); exit(); }',
          trigger=trigger_tcp_loopback)

    # Step 7: ipt_do_table — iptables table evaluation
    check(7, "ipt_do_table (iptables table eval)",
          'kprobe:ipt_do_table { printf("HIT\\n"); exit(); }',
          trigger=trigger_udp_loopback)

    # Step 8: nf_log_packet — netfilter logging
    check(8, "nf_log_packet (netfilter logging)",
          'kprobe:nf_log_packet { printf("HIT\\n"); exit(); }',
          trigger=trigger_udp_loopback)

    # Step 9: nf_conntrack_find_get — conntrack lookup
    check(9, "nf_conntrack_find_get (conntrack lookup)",
          'kprobe:nf_conntrack_find_get { printf("HIT\\n"); exit(); }',
          trigger=trigger_udp_loopback)

    # Step 10: /proc/net/nf_conntrack readable
    step = 10
    name = "/proc/net/nf_conntrack readable"
    try:
        with open("/proc/net/nf_conntrack", "r") as f:
            data = f.read(256)
        # File may be empty if no connections, but must be readable
        status = PASS
    except PermissionError:
        status = SKIP
    except FileNotFoundError:
        status = SKIP
    except Exception:
        status = FAIL
    results.append((step, name, status))
    print(f"  Step {step:2d}: {name:52s} {status}")

    # Summary
    print("\n--- Summary ---")
    total = len(results)
    passed = sum(1 for _, _, s in results if "PASS" in s)
    failed = sum(1 for _, _, s in results if "FAIL" in s)
    skipped = sum(1 for _, _, s in results if "SKIP" in s)
    print(f"  Total: {total}  Passed: {passed}  Failed: {failed}  Skipped: {skipped}")

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
