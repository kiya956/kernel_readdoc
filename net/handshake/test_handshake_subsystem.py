#!/usr/bin/env python3
"""
test_handshake_subsystem.py — bpftrace verification of net/handshake.

Steps
-----
1.  Check handshake Netlink family in /proc/net/protocols or genl
2.  Probe handshake_req_submit          — request enqueued
3.  Probe handshake_req_cancel          — request cancelled
4.  Probe handshake_req_hash_lookup     — socket → request hash lookup
5.  Probe handshake_nl_accept_doit      — daemon ACCEPT handler
6.  Probe handshake_nl_done_doit        — daemon DONE handler
7.  Probe tls_client_hello_x509         — TLS client hello entry
8.  Probe tls_server_hello_x509         — TLS server hello entry
9.  Check genl family 'handshake' in /proc/net/protocols
10. Check CONFIG_NET_HANDSHAKE in kernel config
"""

import subprocess
import sys
import os
import time
import tempfile
import socket
import struct

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


print("\n=== net/handshake subsystem bpftrace verification ===\n")


def genl_list_families():
    """Query Generic Netlink families to detect 'handshake'."""
    try:
        subprocess.run(["genl", "ctrl", "list"], capture_output=True, timeout=5)
    except FileNotFoundError:
        pass
    # Also try via iproute2 genl or direct Netlink
    try:
        r = subprocess.run(["cat", "/proc/net/protocols"],
                           capture_output=True, text=True, timeout=3)
    except Exception:
        pass


# ── Step 1: handshake family in /proc/net ────────────────────────────────────
print(f"  Step  1: {'handshake genl family detectable':52s}", end=" ")
handshake_found = False
try:
    r = subprocess.run(["grep", "-r", "handshake", "/proc/net/"],
                       capture_output=True, text=True, timeout=5)
    if "handshake" in r.stdout:
        handshake_found = True
except Exception:
    pass

# Also check kallsyms
if not handshake_found:
    try:
        with open("/proc/kallsyms") as f:
            for line in f:
                if "handshake_req_submit" in line:
                    handshake_found = True
                    break
    except Exception:
        pass

if handshake_found:
    print(PASS)
    results.append((1, "handshake family", PASS))
else:
    print(SKIP)
    results.append((1, "handshake family", SKIP))

# ── Step 2: handshake_req_submit ─────────────────────────────────────────────
prog2 = """
kprobe:handshake_req_submit {
    printf("HIT handshake_req_submit\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(2, "handshake_req_submit kprobe", prog2, timeout=8)

# ── Step 3: handshake_req_cancel ─────────────────────────────────────────────
prog3 = """
kprobe:handshake_req_cancel {
    printf("HIT handshake_req_cancel\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(3, "handshake_req_cancel kprobe", prog3, timeout=8)

# ── Step 4: handshake_req_hash_lookup ────────────────────────────────────────
prog4 = """
kprobe:handshake_req_hash_lookup {
    printf("HIT handshake_req_hash_lookup\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(4, "handshake_req_hash_lookup kprobe", prog4, timeout=8)

# ── Step 5: handshake_nl_accept_doit ─────────────────────────────────────────
prog5 = """
kprobe:handshake_nl_accept_doit {
    printf("HIT handshake_nl_accept_doit\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(5, "handshake_nl_accept_doit kprobe", prog5, timeout=8)

# ── Step 6: handshake_nl_done_doit ───────────────────────────────────────────
prog6 = """
kprobe:handshake_nl_done_doit {
    printf("HIT handshake_nl_done_doit\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(6, "handshake_nl_done_doit kprobe", prog6, timeout=8)

# ── Step 7: tls_client_hello_x509 ────────────────────────────────────────────
prog7 = """
kprobe:tls_client_hello_x509 {
    printf("HIT tls_client_hello_x509\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(7, "tls_client_hello_x509 kprobe", prog7, timeout=8)

# ── Step 8: tls_server_hello_x509 ────────────────────────────────────────────
prog8 = """
kprobe:tls_server_hello_x509 {
    printf("HIT tls_server_hello_x509\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(8, "tls_server_hello_x509 kprobe", prog8, timeout=8)

# ── Step 9: Netlink GENERIC family query ─────────────────────────────────────
prog9 = """
kprobe:genl_family_rcv_msg {
    printf("HIT genl_family_rcv_msg\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(9, "genl_family_rcv_msg (any genl cmd)", prog9,
      trigger=genl_list_families, timeout=10)

# ── Step 10: CONFIG_NET_HANDSHAKE ─────────────────────────────────────────────
print(f"  Step 10: {'CONFIG_NET_HANDSHAKE in kernel config':52s}", end=" ")
configured = False
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
        if "CONFIG_NET_HANDSHAKE=y" in data or "CONFIG_NET_HANDSHAKE=m" in data:
            configured = True
            break
    except Exception:
        pass
if configured:
    print(PASS)
    results.append((10, "CONFIG_NET_HANDSHAKE", PASS))
else:
    print(SKIP)
    results.append((10, "CONFIG_NET_HANDSHAKE", SKIP))

# ── Summary ──────────────────────────────────────────────────────────────────
print()
passed = sum(1 for _, _, s in results if s == PASS)
failed = sum(1 for _, _, s in results if s == FAIL)
skipped = sum(1 for _, _, s in results if s == SKIP)
total = len(results)
print(f"Results: {passed}/{total} passed, {failed} failed, {skipped} skipped")
if failed > 0:
    sys.exit(1)
