#!/usr/bin/env python3
"""
IPv4 Subsystem – bpftrace probe test
=====================================

Verifies that key IPv4 kernel functions are traceable via bpftrace.

Steps
-----
 1. Probe ip_rcv            — IPv4 packet receive entry
 2. Probe ip_output         — IPv4 packet output
 3. Probe tcp_v4_rcv        — TCP receive path
 4. Probe udp_rcv           — UDP receive path
 5. Probe ip_route_input_noref — route lookup on RX
 6. Probe tcp_connect       — TCP connection initiation
 7. Probe arp_rcv           — ARP receive
 8. Probe icmp_rcv          — ICMP receive
 9. Probe ip_forward        — IP forwarding path
10. Probe tcp_retransmit_skb — TCP retransmit
"""

import subprocess, sys, os, time, tempfile, socket

# ── Colours ──────────────────────────────────────────────────────────
PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
SKIP = "\033[33mSKIP\033[0m"
BPFTRACE = "/usr/bin/bpftrace"
ATTACH_WAIT = 2.0


# ── Helper: run a bpftrace program ──────────────────────────────────
def run_bpftrace(program, trigger=None, timeout=10):
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


# ── Helper: record + print result ───────────────────────────────────
results = []


def check(step_num, name, program, trigger=None, expect="HIT", timeout=10):
    stdout, stderr, skipped = run_bpftrace(program, trigger, timeout)
    if skipped:
        results.append((step_num, name, SKIP))
        print(f"  Step {step_num:2d}: {name:52s} {SKIP}")
        return
    ok = expect in stdout
    status = PASS if ok else FAIL
    results.append((step_num, name, status))
    print(f"  Step {step_num:2d}: {name:52s} {status}")
    if not ok and stdout.strip():
        print(f"            stdout: {stdout.strip()[:200]}")


# ── Trigger helpers ──────────────────────────────────────────────────

def trigger_udp_loopback():
    """Send a UDP datagram to the discard port on loopback."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.sendto(b"ipv4-probe-test", ("127.0.0.1", 9))
    s.close()


def trigger_tcp_loopback():
    """Attempt a TCP connect to loopback port 22 (will likely be refused)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1)
    try:
        s.connect(("127.0.0.1", 22))
    except (ConnectionRefusedError, OSError):
        pass
    finally:
        s.close()


def trigger_ping_loopback():
    """Ping loopback once to generate ICMP echo/reply traffic."""
    subprocess.run(
        ["ping", "-c", "1", "-W", "1", "127.0.0.1"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def trigger_combined_traffic():
    """Generate UDP + TCP + ICMP traffic on loopback."""
    trigger_udp_loopback()
    trigger_tcp_loopback()
    trigger_ping_loopback()


# ── Main ─────────────────────────────────────────────────────────────

def main():
    if os.geteuid() != 0:
        print("ERROR: must run as root (bpftrace needs CAP_BPF).")
        sys.exit(1)

    print("\n  IPv4 Subsystem – bpftrace probe tests\n")

    # Step 1 — ip_rcv
    check(1, "ip_rcv (IPv4 packet receive entry)", """
        kprobe:ip_rcv { printf("HIT\\n"); exit(); }
    """, trigger=trigger_udp_loopback)

    # Step 2 — ip_output
    check(2, "ip_output (IPv4 packet output)", """
        kprobe:ip_output { printf("HIT\\n"); exit(); }
    """, trigger=trigger_udp_loopback)

    # Step 3 — tcp_v4_rcv
    check(3, "tcp_v4_rcv (TCP receive)", """
        kprobe:tcp_v4_rcv { printf("HIT\\n"); exit(); }
    """, trigger=trigger_tcp_loopback)

    # Step 4 — udp_rcv
    check(4, "udp_rcv (UDP receive)", """
        kprobe:udp_rcv { printf("HIT\\n"); exit(); }
    """, trigger=trigger_udp_loopback)

    # Step 5 — ip_route_input_noref
    check(5, "ip_route_input_noref (route lookup)", """
        kprobe:ip_route_input_noref { printf("HIT\\n"); exit(); }
    """, trigger=trigger_udp_loopback)

    # Step 6 — tcp_connect
    check(6, "tcp_connect (TCP connection initiation)", """
        kprobe:tcp_connect { printf("HIT\\n"); exit(); }
    """, trigger=trigger_tcp_loopback)

    # Step 7 — arp_rcv
    # ARP is not used on loopback, so this is best-effort.
    check(7, "arp_rcv (ARP receive)", """
        kprobe:arp_rcv { printf("HIT\\n"); exit(); }
        interval:s:3 { printf("TIMEOUT\\n"); exit(); }
    """, trigger=trigger_combined_traffic, expect="HIT", timeout=8)

    # Step 8 — icmp_rcv
    check(8, "icmp_rcv (ICMP receive)", """
        kprobe:icmp_rcv { printf("HIT\\n"); exit(); }
    """, trigger=trigger_ping_loopback)

    # Step 9 — ip_forward
    # Forwarding on loopback is unlikely; probe-ability is the real test.
    check(9, "ip_forward (IP forwarding)", """
        kprobe:ip_forward { printf("HIT\\n"); exit(); }
        interval:s:3 { printf("TIMEOUT\\n"); exit(); }
    """, trigger=trigger_combined_traffic, expect="HIT", timeout=8)

    # Step 10 — tcp_retransmit_skb
    # Retransmits are rare on loopback; verify the probe attaches.
    check(10, "tcp_retransmit_skb (TCP retransmit)", """
        kprobe:tcp_retransmit_skb { printf("HIT\\n"); exit(); }
        interval:s:3 { printf("TIMEOUT\\n"); exit(); }
    """, trigger=trigger_tcp_loopback, expect="HIT", timeout=8)

    # ── Summary ──────────────────────────────────────────────────────
    print("\n  Summary")
    total = len(results)
    passed = sum(1 for _, _, s in results if s == PASS)
    failed = sum(1 for _, _, s in results if s == FAIL)
    skipped = sum(1 for _, _, s in results if s == SKIP)
    print(f"    Total : {total}")
    print(f"    Passed: {passed}")
    print(f"    Failed: {failed}")
    print(f"    Skipped: {skipped}")
    print()
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
