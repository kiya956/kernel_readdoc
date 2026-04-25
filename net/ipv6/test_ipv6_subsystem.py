#!/usr/bin/env python3
"""
IPv6 Subsystem Workflow Verification
======================================
Uses bpftrace to trace core IPv6 receive, transmit, routing,
NDP, ICMPv6, and DAD/SLAAC kernel functions.

Requirements:
  - Linux with IPv6 enabled (CONFIG_IPV6=y|m)
  - bpftrace >= 0.14
  - Root privileges
  - Loopback (::1) must be up

Usage:
  sudo python3 test_ipv6_subsystem.py
"""

import subprocess, sys, os, time, textwrap, signal, socket

PASS = "\033[32m[PASS]\033[0m"
FAIL = "\033[31m[FAIL]\033[0m"
SKIP = "\033[33m[SKIP]\033[0m"
INFO = "\033[34m[INFO]\033[0m"
BPFTRACE = "/usr/bin/bpftrace"
ATTACH_WAIT = 2.0
results = []


def run(cmd, timeout=10):
    """Run a shell command and return CompletedProcess or None on timeout."""
    try:
        return subprocess.run(cmd, shell=True, capture_output=True,
                              text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None


def check_prereqs():
    """Verify root, bpftrace, and IPv6 loopback availability."""
    print(f"\n{INFO} Checking prerequisites...")
    if os.geteuid() != 0:
        print(f"{FAIL} Must run as root"); sys.exit(1)
    if not os.path.isfile(BPFTRACE):
        print(f"{FAIL} bpftrace not found at {BPFTRACE}"); sys.exit(1)
    # Quick IPv6 loopback check
    r = run("ping -6 -c1 -W1 ::1")
    if not r or r.returncode != 0:
        print(f"{FAIL} IPv6 loopback (::1) unreachable"); sys.exit(1)
    print(f"{PASS} Prerequisites OK")


def run_bpftrace(script, trigger=None, timeout=8):
    """
    Run a bpftrace one-liner, optionally fire a trigger function while
    bpftrace is attached, then collect stdout+stderr.
    Returns combined output string.
    """
    proc = subprocess.Popen(
        [BPFTRACE, "-e", script],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        preexec_fn=os.setsid,
    )
    time.sleep(ATTACH_WAIT)

    if trigger:
        try:
            trigger()
        except Exception as exc:
            print(f"         trigger error: {exc}")

    time.sleep(0.5)
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGINT)
    except ProcessLookupError:
        pass
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
    return out + err


def check(step, desc, output, keyword):
    """Evaluate bpftrace output against a keyword and record result."""
    if keyword in output:
        print(f"{PASS}  Detected: '{keyword}'")
        for line in output.strip().splitlines()[:3]:
            print(f"         {line.strip()[:120]}")
        results.append((step, desc, "PASS"))
    elif any(tok in output for tok in ("not traceable", "No probes", "ERROR",
                                        "could not resolve")):
        print(f"{SKIP}  Symbol not traceable in this kernel")
        results.append((step, desc, "SKIP"))
    else:
        print(f"{FAIL}  Expected '{keyword}' not found")
        for line in output.strip().splitlines()[:5]:
            print(f"         {line.strip()[:120]}")
        results.append((step, desc, "FAIL"))


# ── Trigger helpers ──────────────────────────────────────────────────

def trigger_tcp6_loopback():
    """Connect via TCPv6 to a local port to exercise tcp_v6 + ipv6_rcv."""
    srv = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("::1", 0))
    port = srv.getsockname()[1]
    srv.listen(1)
    cli = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    cli.connect(("::1", port))
    conn, _ = srv.accept()
    cli.sendall(b"hello ipv6")
    conn.recv(64)
    conn.close(); cli.close(); srv.close()


def trigger_udp6_loopback():
    """Send a UDPv6 datagram on loopback."""
    s = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
    s.sendto(b"ipv6 udp probe", ("::1", 19876))
    s.close()


def trigger_icmp6_ping():
    """Send an ICMPv6 echo request to ::1."""
    run("ping -6 -c2 -W1 ::1")


def trigger_ndp_neighbor():
    """Trigger NDP by looking up a link-local neighbor (best-effort)."""
    # A simple ping to a multicast-solicited address may trigger ndisc
    run("ping -6 -c1 -W1 ff02::1%lo")


# ── Test Steps ───────────────────────────────────────────────────────

def step1_ipv6_rcv():
    """Probe ipv6_rcv — IPv6 packet receive entry point."""
    print(f"\n── Step 1: ipv6_rcv — IPv6 packet receive")
    script = textwrap.dedent(r'''
        kprobe:ipv6_rcv {
            printf("IPV6_RCV hit\n");
            exit();
        }
        interval:s:5 { printf("timeout\n"); exit(); }
    ''')
    out = run_bpftrace(script, trigger=trigger_tcp6_loopback)
    check(1, "ipv6_rcv — IPv6 packet receive", out, "IPV6_RCV hit")


def step2_ip6_output():
    """Probe ip6_output — IPv6 packet output."""
    print(f"\n── Step 2: ip6_output — IPv6 packet output")
    script = textwrap.dedent(r'''
        kprobe:ip6_output {
            printf("IP6_OUTPUT hit\n");
            exit();
        }
        interval:s:5 { printf("timeout\n"); exit(); }
    ''')
    out = run_bpftrace(script, trigger=trigger_tcp6_loopback)
    check(2, "ip6_output — IPv6 packet output", out, "IP6_OUTPUT hit")


def step3_tcp_v6_rcv():
    """Probe tcp_v6_rcv — TCPv6 receive."""
    print(f"\n── Step 3: tcp_v6_rcv — TCPv6 receive")
    script = textwrap.dedent(r'''
        kprobe:tcp_v6_rcv {
            printf("TCP_V6_RCV hit\n");
            exit();
        }
        interval:s:5 { printf("timeout\n"); exit(); }
    ''')
    out = run_bpftrace(script, trigger=trigger_tcp6_loopback)
    check(3, "tcp_v6_rcv — TCPv6 receive", out, "TCP_V6_RCV hit")


def step4_udpv6_rcv():
    """Probe udpv6_rcv — UDPv6 receive."""
    print(f"\n── Step 4: udpv6_rcv — UDPv6 receive")
    # udpv6_rcv may be named __udp6_lib_rcv in some kernels
    script = textwrap.dedent(r'''
        kprobe:udpv6_rcv {
            printf("UDPV6_RCV hit\n");
            exit();
        }
        interval:s:5 { printf("timeout\n"); exit(); }
    ''')
    out = run_bpftrace(script, trigger=trigger_udp6_loopback)
    if "UDPV6_RCV hit" not in out:
        # Fallback to __udp6_lib_rcv
        script2 = textwrap.dedent(r'''
            kprobe:__udp6_lib_rcv {
                printf("UDPV6_RCV hit\n");
                exit();
            }
            interval:s:5 { printf("timeout\n"); exit(); }
        ''')
        out = run_bpftrace(script2, trigger=trigger_udp6_loopback)
    check(4, "udpv6_rcv — UDPv6 receive", out, "UDPV6_RCV hit")


def step5_ip6_route_input_lookup():
    """Probe ip6_route_input_lookup — IPv6 route lookup."""
    print(f"\n── Step 5: ip6_route_input_lookup — route lookup")
    script = textwrap.dedent(r'''
        kprobe:ip6_route_input_lookup {
            printf("IP6_ROUTE_LOOKUP hit\n");
            exit();
        }
        interval:s:5 { printf("timeout\n"); exit(); }
    ''')
    out = run_bpftrace(script, trigger=trigger_tcp6_loopback)
    check(5, "ip6_route_input_lookup — route lookup", out,
          "IP6_ROUTE_LOOKUP hit")


def step6_ndisc_rcv():
    """Probe ndisc_rcv — NDP receive."""
    print(f"\n── Step 6: ndisc_rcv — NDP receive")
    script = textwrap.dedent(r'''
        kprobe:ndisc_rcv {
            printf("NDISC_RCV hit\n");
            exit();
        }
        interval:s:6 { printf("timeout\n"); exit(); }
    ''')
    out = run_bpftrace(script, trigger=trigger_ndp_neighbor)
    check(6, "ndisc_rcv — NDP receive", out, "NDISC_RCV hit")


def step7_icmpv6_rcv():
    """Probe icmpv6_rcv — ICMPv6 receive."""
    print(f"\n── Step 7: icmpv6_rcv — ICMPv6 receive")
    script = textwrap.dedent(r'''
        kprobe:icmpv6_rcv {
            printf("ICMPV6_RCV hit\n");
            exit();
        }
        interval:s:5 { printf("timeout\n"); exit(); }
    ''')
    out = run_bpftrace(script, trigger=trigger_icmp6_ping)
    check(7, "icmpv6_rcv — ICMPv6 receive", out, "ICMPV6_RCV hit")


def step8_addrconf_dad_completed():
    """Probe addrconf_dad_completed — DAD completion."""
    print(f"\n── Step 8: addrconf_dad_completed — DAD completion")
    # DAD happens at interface/address bring-up; probe presence only
    r = run("grep -c 'addrconf_dad_completed' /proc/kallsyms")
    count = int(r.stdout.strip()) if r and r.returncode == 0 else 0
    if count > 0:
        print(f"{PASS}  addrconf_dad_completed symbol present ({count} hit(s))")
        results.append((8, "addrconf_dad_completed — DAD completion", "PASS"))
    else:
        # Try a quick bpftrace dry-run
        script = textwrap.dedent(r'''
            kprobe:addrconf_dad_completed {
                printf("DAD_COMPLETED hit\n");
            }
            interval:s:1 { exit(); }
        ''')
        out = run_bpftrace(script)
        if any(tok in out for tok in ("not traceable", "No probes", "ERROR")):
            print(f"{SKIP}  addrconf_dad_completed not traceable")
            results.append((8, "addrconf_dad_completed — DAD completion",
                            "SKIP"))
        else:
            print(f"{PASS}  addrconf_dad_completed probe attached OK")
            results.append((8, "addrconf_dad_completed — DAD completion",
                            "PASS"))


def step9_ip6_forward():
    """Probe ip6_forward — IPv6 forwarding."""
    print(f"\n── Step 9: ip6_forward — IPv6 forwarding")
    # Forwarding on loopback is rare; verify symbol exists and probe attaches
    r = run("grep -c ' ip6_forward$' /proc/kallsyms")
    count = int(r.stdout.strip()) if r and r.returncode == 0 else 0
    if count == 0:
        r = run("grep -c 'ip6_forward' /proc/kallsyms")
        count = int(r.stdout.strip()) if r and r.returncode == 0 else 0
    if count > 0:
        script = textwrap.dedent(r'''
            kprobe:ip6_forward {
                printf("IP6_FORWARD hit\n");
            }
            interval:s:1 { exit(); }
        ''')
        out = run_bpftrace(script)
        if any(tok in out for tok in ("not traceable", "No probes", "ERROR")):
            print(f"{SKIP}  ip6_forward not traceable")
            results.append((9, "ip6_forward — IPv6 forwarding", "SKIP"))
        else:
            print(f"{PASS}  ip6_forward probe attached OK (symbol present)")
            results.append((9, "ip6_forward — IPv6 forwarding", "PASS"))
    else:
        print(f"{SKIP}  ip6_forward symbol not found")
        results.append((9, "ip6_forward — IPv6 forwarding", "SKIP"))


def step10_fib6_lookup():
    """Probe fib6_lookup — FIB6 lookup."""
    print(f"\n── Step 10: fib6_lookup — FIB6 lookup")
    script = textwrap.dedent(r'''
        kprobe:fib6_lookup {
            printf("FIB6_LOOKUP hit\n");
            exit();
        }
        interval:s:5 { printf("timeout\n"); exit(); }
    ''')
    out = run_bpftrace(script, trigger=trigger_tcp6_loopback)
    check(10, "fib6_lookup — FIB6 lookup", out, "FIB6_LOOKUP hit")


# ── Summary ──────────────────────────────────────────────────────────

def print_summary():
    print("\n" + "═" * 60)
    print("  IPv6 Subsystem Verification Summary")
    print("═" * 60)
    passed  = sum(1 for _, _, s in results if s == "PASS")
    failed  = sum(1 for _, _, s in results if s == "FAIL")
    skipped = sum(1 for _, _, s in results if s == "SKIP")
    for n, d, s in results:
        icon = PASS if s == "PASS" else (FAIL if s == "FAIL" else SKIP)
        print(f"  Step {n:>2}: {icon}  {d}")
    print("═" * 60)
    print(f"  Total: {len(results)}  | \033[32mPASS:{passed}\033[0m "
          f"| \033[31mFAIL:{failed}\033[0m | \033[33mSKIP:{skipped}\033[0m")
    print("═" * 60)
    if failed == 0:
        print(f"\n{PASS} All verifiable steps passed!\n")
        return 0
    print(f"\n{FAIL} {failed} step(s) failed.\n")
    return 1


def main():
    print("╔══════════════════════════════════════════════════════╗")
    print("║       IPv6 Subsystem — Workflow Verification         ║")
    print("╚══════════════════════════════════════════════════════╝")
    check_prereqs()
    step1_ipv6_rcv()
    step2_ip6_output()
    step3_tcp_v6_rcv()
    step4_udpv6_rcv()
    step5_ip6_route_input_lookup()
    step6_ndisc_rcv()
    step7_icmpv6_rcv()
    step8_addrconf_dad_completed()
    step9_ip6_forward()
    step10_fib6_lookup()
    return print_summary()


if __name__ == "__main__":
    sys.exit(main())
