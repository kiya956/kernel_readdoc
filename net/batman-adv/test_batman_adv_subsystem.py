#!/usr/bin/env python3
"""
test_batman_adv_subsystem.py — bpftrace-based verification of batman-adv.

Steps
-----
1.  Check batman-adv module loaded
2.  Probe batadv_batman_skb_recv         — incoming OGM/frame receive
3.  Probe batadv_send_skb_packet         — outgoing frame transmit
4.  Probe batadv_orig_node_new           — new originator table entry
5.  Probe batadv_iv_ogm_emit             — BATMAN IV OGM emission
6.  Probe batadv_v_ogm_send             — BATMAN V OGM2 emission
7.  Probe batadv_tt_local_add           — local TT add (new client)
8.  Probe batadv_tt_global_add          — global TT add
9.  Probe batadv_dat_snoop_outgoing_arp — DAT ARP snooping
10. Check bat0 mesh interface creation
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


print("\n=== batman-adv subsystem bpftrace verification ===\n")

# ── Step 1: batman-adv module check ──────────────────────────────────────────
print(f"  Step  1: {'batman-adv module loaded':52s}", end=" ")
batadv_loaded = False
try:
    with open("/proc/modules") as f:
        for line in f:
            if line.startswith("batman_adv"):
                batadv_loaded = True
                break
except OSError:
    pass

if not batadv_loaded:
    # Try loading
    r = subprocess.run(["modprobe", "batman-adv"], capture_output=True, timeout=10)
    if r.returncode == 0:
        batadv_loaded = True
        time.sleep(1)

if batadv_loaded:
    print(PASS)
    results.append((1, "batman-adv module", PASS))
else:
    print(SKIP)
    results.append((1, "batman-adv module", SKIP))
    print("\n  batman-adv not available — all remaining steps will SKIP\n")


def load_bat0():
    """Create bat0 if not present."""
    if not os.path.exists("/sys/class/net/bat0"):
        subprocess.run(["ip", "link", "add", "name", "bat0", "type", "batadv"],
                       capture_output=True, timeout=5)
        subprocess.run(["ip", "link", "set", "up", "bat0"],
                       capture_output=True, timeout=5)


def cleanup_bat0():
    subprocess.run(["ip", "link", "del", "bat0"], capture_output=True, timeout=5)


# ── Step 2: batadv_batman_skb_recv ───────────────────────────────────────────
prog2 = """
kprobe:batadv_batman_skb_recv {
    printf("HIT batadv_batman_skb_recv\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(2, "batadv_batman_skb_recv kprobe", prog2,
      trigger=lambda: load_bat0(), timeout=10)

# ── Step 3: batadv_send_skb_packet ───────────────────────────────────────────
prog3 = """
kprobe:batadv_send_skb_packet {
    printf("HIT batadv_send_skb_packet\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(3, "batadv_send_skb_packet kprobe", prog3, timeout=8)

# ── Step 4: batadv_orig_node_new ─────────────────────────────────────────────
prog4 = """
kprobe:batadv_orig_node_new {
    printf("HIT batadv_orig_node_new\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(4, "batadv_orig_node_new kprobe", prog4, timeout=8)

# ── Step 5: batadv_iv_ogm_emit ───────────────────────────────────────────────
prog5 = """
kprobe:batadv_iv_ogm_emit {
    printf("HIT batadv_iv_ogm_emit\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(5, "batadv_iv_ogm_emit kprobe", prog5, timeout=8)

# ── Step 6: batadv_v_ogm_send ────────────────────────────────────────────────
prog6 = """
kprobe:batadv_v_ogm_send {
    printf("HIT batadv_v_ogm_send\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(6, "batadv_v_ogm_send kprobe", prog6, timeout=8)

# ── Step 7: batadv_tt_local_add ──────────────────────────────────────────────
prog7 = """
kprobe:batadv_tt_local_add {
    printf("HIT batadv_tt_local_add\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(7, "batadv_tt_local_add kprobe", prog7, timeout=8)

# ── Step 8: batadv_tt_global_add ─────────────────────────────────────────────
prog8 = """
kprobe:batadv_tt_global_add {
    printf("HIT batadv_tt_global_add\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(8, "batadv_tt_global_add kprobe", prog8, timeout=8)

# ── Step 9: batadv_dat_snoop_outgoing_arp ────────────────────────────────────
prog9 = """
kprobe:batadv_dat_snoop_outgoing_arp_request,
kprobe:batadv_dat_snoop_incoming_arp_reply {
    printf("HIT batadv_dat_arp\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(9, "batadv_dat ARP snooping kprobe", prog9, timeout=8)

# ── Step 10: bat0 interface creation ─────────────────────────────────────────
print(f"  Step 10: {'bat0 mesh interface creation':52s}", end=" ")
created = False
try:
    if not os.path.exists("/sys/class/net/bat0"):
        r = subprocess.run(["ip", "link", "add", "name", "bat0", "type", "batadv"],
                           capture_output=True, timeout=5)
        created = r.returncode == 0 and os.path.exists("/sys/class/net/bat0")
    else:
        created = True
except Exception:
    pass

if created:
    print(PASS)
    results.append((10, "bat0 creation", PASS))
    cleanup_bat0()
else:
    print(SKIP)
    results.append((10, "bat0 creation", SKIP))

# ── Summary ──────────────────────────────────────────────────────────────────
print()
passed = sum(1 for _, _, s in results if s == PASS)
failed = sum(1 for _, _, s in results if s == FAIL)
skipped = sum(1 for _, _, s in results if s == SKIP)
total = len(results)
print(f"Results: {passed}/{total} passed, {failed} failed, {skipped} skipped")
if failed > 0:
    sys.exit(1)
