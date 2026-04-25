#!/usr/bin/env python3
"""
test_packet_subsystem.py — bpftrace-based verification of the AF_PACKET subsystem.

Steps
-----
1.  Probe packet_create                — AF_PACKET socket creation
2.  Probe packet_rcv                   — raw packet receive path
3.  Probe tpacket_rcv                  — TPACKET mmap receive path
4.  Probe packet_sendmsg               — packet transmit path
5.  Probe packet_bind                  — bind to interface
6.  Probe packet_setsockopt            — setsockopt handler
7.  Probe packet_getsockopt            — getsockopt handler
8.  Probe packet_poll                  — poll/select readiness
9.  Probe packet_release               — socket close/cleanup
10. Run tcpdump capture and verify     — userspace round-trip
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


print("\n=== AF_PACKET subsystem bpftrace verification ===\n")

# ── helpers ────────────────────────────────────────────────────────────────

PACKET_SOCKET_SNIPPET = r"""
import socket, struct, time
ETH_P_ALL = 0x0003
s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(ETH_P_ALL))
s.bind(("lo", 0))
time.sleep(0.3)
s.close()
"""

def trigger_packet_socket():
    """Create an AF_PACKET socket, bind to lo, then close."""
    subprocess.run(
        [sys.executable, "-c", PACKET_SOCKET_SNIPPET],
        timeout=8, capture_output=True,
    )

def trigger_packet_socket_with_traffic():
    """Create AF_PACKET socket on lo and generate some loopback traffic."""
    snippet = r"""
import socket, struct, time, threading
ETH_P_ALL = 0x0003
s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(ETH_P_ALL))
s.bind(("lo", 0))
# generate loopback traffic so packet_rcv fires
def ping():
    import subprocess
    subprocess.run(["ping", "-c", "2", "-i", "0.2", "127.0.0.1"],
                   capture_output=True, timeout=5)
t = threading.Thread(target=ping)
t.start()
time.sleep(1.5)
s.close()
t.join(timeout=3)
"""
    subprocess.run(
        [sys.executable, "-c", snippet],
        timeout=10, capture_output=True,
    )

def trigger_tcpdump_brief():
    """Run tcpdump on lo for a short capture while generating traffic."""
    ping = subprocess.Popen(
        ["ping", "-c", "3", "-i", "0.2", "127.0.0.1"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        subprocess.run(
            ["tcpdump", "-i", "lo", "-c", "3", "-nn", "--immediate-mode"],
            timeout=6, capture_output=True,
        )
    except FileNotFoundError:
        pass
    except subprocess.TimeoutExpired:
        pass
    finally:
        ping.kill()
        ping.wait()

def trigger_sendmsg():
    """Send a raw frame on the loopback interface."""
    snippet = r"""
import socket, struct
ETH_P_ALL = 0x0003
s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(ETH_P_ALL))
s.bind(("lo", 0))
# craft a minimal ethernet frame (dst + src + ethertype + payload)
frame = b'\xff'*6 + b'\x00'*6 + struct.pack('!H', 0x0800) + b'\x00'*46
try:
    s.send(frame)
except OSError:
    pass
s.close()
"""
    subprocess.run(
        [sys.executable, "-c", snippet],
        timeout=8, capture_output=True,
    )

def trigger_setsockopt():
    """Create AF_PACKET socket and set PACKET_ADD_MEMBERSHIP (promisc)."""
    snippet = r"""
import socket, struct, time
ETH_P_ALL = 0x0003
PACKET_ADD_MEMBERSHIP = 1
PACKET_MR_PROMISC = 1
SOL_PACKET = 263
s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(ETH_P_ALL))
s.bind(("lo", 0))
# struct packet_mreq { ifindex, type, alen, address }
ifindex = socket.if_nametoindex("lo")
mreq = struct.pack("IHH8s", ifindex, PACKET_MR_PROMISC, 0, b'\x00'*8)
try:
    s.setsockopt(SOL_PACKET, PACKET_ADD_MEMBERSHIP, mreq)
except OSError:
    pass
time.sleep(0.2)
s.close()
"""
    subprocess.run(
        [sys.executable, "-c", snippet],
        timeout=8, capture_output=True,
    )

def trigger_getsockopt():
    """Create AF_PACKET socket and call getsockopt for PACKET_STATISTICS."""
    snippet = r"""
import socket, struct, time
ETH_P_ALL = 0x0003
SOL_PACKET = 263
PACKET_STATISTICS = 6
s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(ETH_P_ALL))
s.bind(("lo", 0))
try:
    s.getsockopt(SOL_PACKET, PACKET_STATISTICS, 8)
except OSError:
    pass
time.sleep(0.1)
s.close()
"""
    subprocess.run(
        [sys.executable, "-c", snippet],
        timeout=8, capture_output=True,
    )

def trigger_poll():
    """Create AF_PACKET socket and poll() it."""
    snippet = r"""
import socket, select, time
ETH_P_ALL = 0x0003
s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(ETH_P_ALL))
s.bind(("lo", 0))
select.poll().register(s, select.POLLIN)
p = select.poll()
p.register(s, select.POLLIN)
p.poll(200)  # 200 ms timeout
s.close()
"""
    subprocess.run(
        [sys.executable, "-c", snippet],
        timeout=8, capture_output=True,
    )


# ── Step 1: packet_create ─────────────────────────────────────────────────

check(1, "packet_create — AF_PACKET socket creation",
      r"""
kprobe:packet_create {
    printf("HIT packet_create\n");
    exit();
}
""",
      trigger=trigger_packet_socket)

# ── Step 2: packet_rcv ────────────────────────────────────────────────────

check(2, "packet_rcv — raw packet receive path",
      r"""
kprobe:packet_rcv {
    printf("HIT packet_rcv\n");
    exit();
}
""",
      trigger=trigger_packet_socket_with_traffic)

# ── Step 3: tpacket_rcv ──────────────────────────────────────────────────

check(3, "tpacket_rcv — TPACKET mmap receive path",
      r"""
kprobe:tpacket_rcv {
    printf("HIT tpacket_rcv\n");
    exit();
}
""",
      trigger=trigger_tcpdump_brief)

# ── Step 4: packet_sendmsg ───────────────────────────────────────────────

check(4, "packet_sendmsg — packet transmit path",
      r"""
kprobe:packet_sendmsg {
    printf("HIT packet_sendmsg\n");
    exit();
}
""",
      trigger=trigger_sendmsg)

# ── Step 5: packet_bind ──────────────────────────────────────────────────

check(5, "packet_bind — bind to interface",
      r"""
kprobe:packet_bind {
    printf("HIT packet_bind\n");
    exit();
}
""",
      trigger=trigger_packet_socket)

# ── Step 6: packet_setsockopt ────────────────────────────────────────────

check(6, "packet_setsockopt — setsockopt handler",
      r"""
kprobe:packet_setsockopt {
    printf("HIT packet_setsockopt\n");
    exit();
}
""",
      trigger=trigger_setsockopt)

# ── Step 7: packet_getsockopt ────────────────────────────────────────────

check(7, "packet_getsockopt — getsockopt handler",
      r"""
kprobe:packet_getsockopt {
    printf("HIT packet_getsockopt\n");
    exit();
}
""",
      trigger=trigger_getsockopt)

# ── Step 8: packet_poll ──────────────────────────────────────────────────

check(8, "packet_poll — poll/select readiness",
      r"""
kprobe:packet_poll {
    printf("HIT packet_poll\n");
    exit();
}
""",
      trigger=trigger_poll)

# ── Step 9: packet_release ───────────────────────────────────────────────

check(9, "packet_release — socket close/cleanup",
      r"""
kprobe:packet_release {
    printf("HIT packet_release\n");
    exit();
}
""",
      trigger=trigger_packet_socket)

# ── Step 10: userspace round-trip via tcpdump ────────────────────────────

def step10_trigger():
    """Run tcpdump on lo while pinging localhost; verify captured packets."""
    ping = subprocess.Popen(
        ["ping", "-c", "4", "-i", "0.2", "127.0.0.1"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        result = subprocess.run(
            ["tcpdump", "-i", "lo", "-c", "2", "-nn", "--immediate-mode"],
            timeout=8, capture_output=True, text=True,
        )
        captured = result.stdout + result.stderr
        if "packets captured" in captured or "127.0.0.1" in captured:
            print("HIT_TCPDUMP_ROUNDTRIP")
    except FileNotFoundError:
        print("HIT_TCPDUMP_ROUNDTRIP")  # tcpdump not installed, count as skip
    except subprocess.TimeoutExpired:
        pass
    finally:
        ping.kill()
        ping.wait()

# For step 10 we don't use bpftrace; we test tcpdump directly.
print(f"  Step 10: {'tcpdump capture — userspace round-trip':50s} ", end="")
try:
    ping = subprocess.Popen(
        ["ping", "-c", "5", "-i", "0.2", "127.0.0.1"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        result = subprocess.run(
            ["tcpdump", "-i", "lo", "-c", "2", "-nn", "--immediate-mode"],
            timeout=10, capture_output=True, text=True,
        )
        combined = result.stdout + result.stderr
        if "packets captured" in combined or "127.0.0.1" in combined:
            results.append((10, "tcpdump capture — userspace round-trip", PASS))
            print(PASS)
        else:
            results.append((10, "tcpdump capture — userspace round-trip", FAIL))
            print(FAIL)
            print(f"            output: {combined.strip()[:200]}")
    except FileNotFoundError:
        results.append((10, "tcpdump capture — userspace round-trip", SKIP))
        print(SKIP)
        print("            tcpdump not found")
    except subprocess.TimeoutExpired:
        results.append((10, "tcpdump capture — userspace round-trip", FAIL))
        print(FAIL)
        print("            tcpdump timed out")
    finally:
        ping.kill()
        ping.wait()
except Exception as e:
    results.append((10, "tcpdump capture — userspace round-trip", FAIL))
    print(FAIL)
    print(f"            error: {e}")

# ── Summary ──────────────────────────────────────────────────────────────

print()
passed = sum(1 for _, _, s in results if s == PASS)
failed = sum(1 for _, _, s in results if s == FAIL)
skipped = sum(1 for _, _, s in results if s == SKIP)
total = len(results)
print(f"Results: {passed}/{total} passed, {failed} failed, {skipped} skipped")
if failed > 0:
    sys.exit(1)
