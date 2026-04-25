#!/usr/bin/env python3
"""
test_core_subsystem.py — bpftrace verification of net/core packet flow.

Steps
-----
1.  Probe netif_receive_skb     — RX packet entry
2.  Probe dev_queue_xmit        — TX packet entry
3.  Probe __alloc_skb            — sk_buff allocation
4.  Probe kfree_skb_reason       — sk_buff free with drop reason
5.  Probe napi_schedule_prep     — NAPI scheduling
6.  Probe napi_complete_done     — NAPI completion
7.  Probe sock_sendmsg           — socket send path
8.  Probe sock_recvmsg           — socket receive path
9.  Probe neigh_resolve_output   — neighbour resolution
10. Probe dst_alloc              — destination cache allocation
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


def trigger_network_traffic():
    """Generate traffic by doing a UDP send to loopback."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.sendto(b"test-core-probe", ("127.0.0.1", 9))
        s.close()
    except Exception:
        pass


def trigger_tcp_traffic():
    """Generate TCP traffic via loopback."""
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

    print("\n=== net/core subsystem — bpftrace verification ===\n")

    # Step 1: netif_receive_skb — RX entry
    check(1, "netif_receive_skb (RX entry)",
          'kprobe:netif_receive_skb { printf("HIT\\n"); exit(); }',
          trigger=trigger_network_traffic)

    # Step 2: dev_queue_xmit — TX entry
    check(2, "dev_queue_xmit (TX entry)",
          'kprobe:dev_queue_xmit { printf("HIT\\n"); exit(); }',
          trigger=trigger_network_traffic)

    # Step 3: __alloc_skb — sk_buff allocation
    check(3, "__alloc_skb (sk_buff alloc)",
          'kprobe:__alloc_skb { printf("HIT\\n"); exit(); }',
          trigger=trigger_network_traffic)

    # Step 4: kfree_skb_reason — sk_buff free
    check(4, "kfree_skb_reason (sk_buff free)",
          'kprobe:kfree_skb_reason { printf("HIT\\n"); exit(); }',
          trigger=trigger_network_traffic)

    # Step 5: napi_schedule_prep — NAPI scheduling
    check(5, "napi_schedule_prep (NAPI schedule)",
          'kprobe:napi_schedule_prep { printf("HIT\\n"); exit(); }',
          trigger=trigger_network_traffic)

    # Step 6: napi_complete_done — NAPI completion
    check(6, "napi_complete_done (NAPI complete)",
          'kprobe:napi_complete_done { printf("HIT\\n"); exit(); }',
          trigger=trigger_network_traffic)

    # Step 7: sock_sendmsg — socket send
    check(7, "sock_sendmsg (socket send path)",
          'kprobe:sock_sendmsg { printf("HIT\\n"); exit(); }',
          trigger=trigger_network_traffic)

    # Step 8: sock_recvmsg — socket receive
    check(8, "sock_recvmsg (socket recv path)",
          'kprobe:sock_recvmsg { printf("HIT\\n"); exit(); }',
          trigger=trigger_tcp_traffic)

    # Step 9: neigh_resolve_output — neighbour resolution
    check(9, "neigh_resolve_output (neighbour resolve)",
          'kprobe:neigh_resolve_output { printf("HIT\\n"); exit(); }',
          trigger=trigger_tcp_traffic)

    # Step 10: dst_alloc — destination cache
    check(10, "dst_alloc (dst cache alloc)",
          'kprobe:dst_alloc { printf("HIT\\n"); exit(); }',
          trigger=trigger_network_traffic)

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
