#!/usr/bin/env python3
"""
test_netlink_subsystem.py — bpftrace verification of Netlink subsystem.

Steps
-----
1.  Probe netlink_unicast        — unicast message delivery
2.  Probe netlink_broadcast      — broadcast to multicast group
3.  Probe netlink_sendmsg        — netlink socket send path
4.  Probe netlink_recvmsg        — netlink socket receive path
5.  Probe genl_rcv_msg           — generic netlink message dispatch
6.  Probe nlmsg_new              — new netlink message allocation
7.  Probe netlink_create         — netlink socket creation
8.  Probe netlink_bind           — netlink socket bind
9.  Probe netlink_dump           — netlink dump operation
10. Check /proc/net/netlink readable
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
        print(f"  Step {step_num:2d}: {name:52s} {SKIP}")
        return
    ok = expect in stdout
    status = PASS if ok else FAIL
    results.append((step_num, name, status))
    print(f"  Step {step_num:2d}: {name:52s} {status}")
    if not ok:
        if stdout.strip():
            print(f"            stdout: {stdout.strip()[:200]}")
        if stderr.strip():
            print(f"            stderr: {stderr.strip()[:200]}")


print("\n=== netlink subsystem bpftrace verification ===\n")


def netlink_trigger():
    """Open an AF_NETLINK socket and send an RTM_GETLINK dump request."""
    try:
        s = socket.socket(socket.AF_NETLINK, socket.SOCK_DGRAM, 0)  # NETLINK_ROUTE
        s.bind((0, 0))
        # RTM_GETLINK dump request: nlmsghdr(20 bytes) + ifinfomsg(16 bytes)
        import struct
        nlmsg_len = 36  # 16 (nlmsghdr) + 16 (ifinfomsg) + 4 (padding)
        RTM_GETLINK = 18
        NLM_F_REQUEST = 0x01
        NLM_F_DUMP = 0x300
        flags = NLM_F_REQUEST | NLM_F_DUMP
        seq = 1
        pid = 0
        # nlmsghdr
        hdr = struct.pack("=IHHII", nlmsg_len, RTM_GETLINK, flags, seq, pid)
        # ifinfomsg: family=AF_UNSPEC, pad, type, index, flags, change
        ifinfo = struct.pack("=BBHiII", 0, 0, 0, 0, 0, 0)
        msg = hdr + ifinfo
        # pad to nlmsg_len
        msg = msg.ljust(nlmsg_len, b'\x00')
        s.send(msg)
        # receive response to trigger recvmsg path
        try:
            while True:
                data = s.recv(65536)
                if not data:
                    break
                # Check for NLMSG_DONE
                msg_type = struct.unpack("=I", data[4:8])[0] if len(data) >= 8 else 0
                if msg_type == 3:  # NLMSG_DONE
                    break
        except Exception:
            pass
        s.close()
    except Exception:
        pass


def ip_link_trigger():
    """Run 'ip link show' to exercise netlink paths."""
    try:
        subprocess.run(["ip", "link", "show"], capture_output=True, timeout=5)
    except Exception:
        pass


def netlink_socket_trigger():
    """Create and bind an AF_NETLINK socket."""
    try:
        s = socket.socket(socket.AF_NETLINK, socket.SOCK_DGRAM, 0)
        s.bind((0, 0))
        s.close()
    except Exception:
        pass


# ── Step 1: netlink_unicast ──────────────────────────────────────────────────
prog1 = """
kprobe:netlink_unicast {
    printf("HIT netlink_unicast\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(1, "netlink_unicast kprobe", prog1, trigger=netlink_trigger, timeout=10)

# ── Step 2: netlink_broadcast ────────────────────────────────────────────────
prog2 = """
kprobe:netlink_broadcast {
    printf("HIT netlink_broadcast\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(2, "netlink_broadcast kprobe", prog2, trigger=ip_link_trigger, timeout=10)

# ── Step 3: netlink_sendmsg ──────────────────────────────────────────────────
prog3 = """
kprobe:netlink_sendmsg {
    printf("HIT netlink_sendmsg\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(3, "netlink_sendmsg kprobe", prog3, trigger=netlink_trigger, timeout=10)

# ── Step 4: netlink_recvmsg ──────────────────────────────────────────────────
prog4 = """
kprobe:netlink_recvmsg {
    printf("HIT netlink_recvmsg\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(4, "netlink_recvmsg kprobe", prog4, trigger=netlink_trigger, timeout=10)

# ── Step 5: genl_rcv_msg ────────────────────────────────────────────────────
prog5 = """
kprobe:genl_rcv_msg {
    printf("HIT genl_rcv_msg\\n");
    exit();
}
interval:s:5 { exit(); }
"""


def genl_trigger():
    """Send a CTRL_CMD_GETFAMILY dump to generic netlink to trigger genl_rcv_msg."""
    try:
        import struct
        NETLINK_GENERIC = 16
        s = socket.socket(socket.AF_NETLINK, socket.SOCK_DGRAM, NETLINK_GENERIC)
        s.bind((0, 0))
        # GENL ctrl family id is always 0x10 (GENL_ID_CTRL = 16)
        GENL_ID_CTRL = 16
        CTRL_CMD_GETFAMILY = 3
        NLM_F_REQUEST = 0x01
        NLM_F_DUMP = 0x300
        # nlmsghdr (16 bytes) + genlmsghdr (4 bytes)
        nlmsg_len = 20
        hdr = struct.pack("=IHHII", nlmsg_len, GENL_ID_CTRL,
                          NLM_F_REQUEST | NLM_F_DUMP, 1, 0)
        genlhdr = struct.pack("=BBH", CTRL_CMD_GETFAMILY, 1, 0)
        s.send(hdr + genlhdr)
        try:
            s.settimeout(2)
            while True:
                data = s.recv(65536)
                if not data:
                    break
                msg_type = struct.unpack("=HH", data[4:8])[0] if len(data) >= 8 else 0
                if msg_type == 3:  # NLMSG_DONE
                    break
        except Exception:
            pass
        s.close()
    except Exception:
        pass


check(5, "genl_rcv_msg kprobe", prog5, trigger=genl_trigger, timeout=10)

# ── Step 6: nlmsg_new ───────────────────────────────────────────────────────
prog6 = """
kprobe:nlmsg_new {
    printf("HIT nlmsg_new\\n");
    exit();
}
kprobe:__nlmsg_new {
    printf("HIT nlmsg_new\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(6, "nlmsg_new kprobe", prog6, trigger=netlink_trigger, timeout=10)

# ── Step 7: netlink_create ───────────────────────────────────────────────────
prog7 = """
kprobe:netlink_create {
    printf("HIT netlink_create\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(7, "netlink_create kprobe", prog7, trigger=netlink_socket_trigger, timeout=10)

# ── Step 8: netlink_bind ─────────────────────────────────────────────────────
prog8 = """
kprobe:netlink_bind {
    printf("HIT netlink_bind\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(8, "netlink_bind kprobe", prog8, trigger=netlink_socket_trigger, timeout=10)

# ── Step 9: netlink_dump ─────────────────────────────────────────────────────
prog9 = """
kprobe:netlink_dump {
    printf("HIT netlink_dump\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(9, "netlink_dump kprobe", prog9, trigger=netlink_trigger, timeout=10)

# ── Step 10: /proc/net/netlink ───────────────────────────────────────────────
print(f"  Step 10: {'/proc/net/netlink readable':52s}", end=" ")
try:
    with open("/proc/net/netlink") as f:
        data = f.read()
    if len(data) > 0:
        print(PASS)
        results.append((10, "/proc/net/netlink readable", PASS))
    else:
        print(FAIL)
        results.append((10, "/proc/net/netlink readable", FAIL))
except Exception:
    print(SKIP)
    results.append((10, "/proc/net/netlink readable", SKIP))

# ── Summary ──────────────────────────────────────────────────────────────────
print()
passed = sum(1 for _, _, s in results if s == PASS)
failed = sum(1 for _, _, s in results if s == FAIL)
skipped = sum(1 for _, _, s in results if s == SKIP)
total = len(results)
print(f"Results: {passed}/{total} passed, {failed} failed, {skipped} skipped")
if failed > 0:
    sys.exit(1)
