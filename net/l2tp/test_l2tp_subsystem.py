#!/usr/bin/env python3
"""
L2TP Subsystem Workflow Verification
======================================
Uses bpftrace to trace L2TP tunnel creation, session management,
and packet encapsulation/decapsulation.

Requirements:
  - Linux with L2TP (CONFIG_L2TP=y/m)
  - bpftrace >= 0.14
  - Root privileges

Usage:
  sudo python3 test_l2tp_subsystem.py
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


def check_prereqs():
    """Verify basic prerequisites for running this test suite."""
    if os.geteuid() != 0:
        print("  [WARN] Not running as root — bpftrace steps may fail\n")
    if not os.path.isfile(BPFTRACE):
        print(f"  [WARN] bpftrace not found at {BPFTRACE}\n")


def bpf_step(step_num: int, name: str, program: str, trigger=None,
             expect: str = "HIT", timeout: int = 10):
    """Run a bpftrace probe step and record the result."""
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


def run():
    """Execute all 10 verification steps."""

    # ── Step 1: l2tp_ symbols in kallsyms ────────────────────────────────────
    print(f"  Step  1: {'l2tp_ symbols in /proc/kallsyms':52s}", end=" ")
    l2tp_present = False
    try:
        with open("/proc/kallsyms") as f:
            for line in f:
                if "l2tp_recv_common" in line or "l2tp_xmit_skb" in line:
                    l2tp_present = True
                    break
    except Exception:
        pass
    if l2tp_present:
        print(PASS)
        results.append((1, "l2tp kallsyms", PASS))
    else:
        print(SKIP)
        results.append((1, "l2tp kallsyms", SKIP))

    # ── Step 2: l2tp_core module loaded ──────────────────────────────────────
    print(f"  Step  2: {'l2tp_core module loaded':52s}", end=" ")
    l2tp_loaded = False
    try:
        with open("/proc/modules") as f:
            for line in f:
                if line.startswith("l2tp_core"):
                    l2tp_loaded = True
                    break
    except OSError:
        pass
    if not l2tp_loaded:
        # Check if compiled built-in via kallsyms
        try:
            with open("/proc/kallsyms") as f:
                for line in f:
                    if "l2tp_tunnel_create" in line:
                        l2tp_loaded = True
                        break
        except Exception:
            pass
    if l2tp_loaded:
        print(PASS)
        results.append((2, "l2tp_core loaded", PASS))
    else:
        # Try loading the module
        r = subprocess.run(["modprobe", "l2tp_core"], capture_output=True, timeout=10)
        if r.returncode == 0:
            print(PASS)
            results.append((2, "l2tp_core loaded", PASS))
            time.sleep(1)
        else:
            print(SKIP)
            results.append((2, "l2tp_core loaded", SKIP))

    # ── Step 3: l2tp_recv_common ─────────────────────────────────────────────
    prog3 = """
kprobe:l2tp_recv_common {
    printf("HIT l2tp_recv_common\\n");
    exit();
}
interval:s:5 { exit(); }
"""
    bpf_step(3, "l2tp_recv_common kprobe", prog3, timeout=8)

    # ── Step 4: l2tp_xmit_skb ───────────────────────────────────────────────
    prog4 = """
kprobe:l2tp_xmit_skb {
    printf("HIT l2tp_xmit_skb\\n");
    exit();
}
interval:s:5 { exit(); }
"""
    bpf_step(4, "l2tp_xmit_skb kprobe", prog4, timeout=8)

    # ── Step 5: l2tp_tunnel_create ───────────────────────────────────────────
    prog5 = """
kprobe:l2tp_tunnel_create {
    printf("HIT l2tp_tunnel_create\\n");
    exit();
}
interval:s:5 { exit(); }
"""
    bpf_step(5, "l2tp_tunnel_create kprobe", prog5, timeout=8)

    # ── Step 6: tunnel listing via procfs / debugfs ──────────────────────────
    print(f"  Step  6: {'L2TP tunnel listing (debugfs/procfs)':52s}", end=" ")
    tunnel_info = False
    tunnel_paths = [
        "/sys/kernel/debug/l2tp/tunnels",
        "/proc/net/pppol2tp",
    ]
    for p in tunnel_paths:
        if os.path.exists(p):
            tunnel_info = True
            break
    if tunnel_info:
        print(PASS)
        results.append((6, "l2tp tunnel listing", PASS))
    else:
        print(SKIP)
        results.append((6, "l2tp tunnel listing", SKIP))

    # ── Step 7: l2tp_session_create ──────────────────────────────────────────
    prog7 = """
kprobe:l2tp_session_create {
    printf("HIT l2tp_session_create\\n");
    exit();
}
interval:s:5 { exit(); }
"""
    bpf_step(7, "l2tp_session_create kprobe", prog7, timeout=8)

    # ── Step 8: l2tp_netlink — tunnel get ────────────────────────────────────
    prog8 = """
kprobe:l2tp_nl_cmd_tunnel_get,
kprobe:l2tp_nl_tunnel_send {
    printf("HIT l2tp_nl_tunnel\\n");
    exit();
}
interval:s:5 { exit(); }
"""

    def trigger_nl():
        subprocess.run(["ip", "l2tp", "show", "tunnel"],
                       capture_output=True, timeout=5)

    bpf_step(8, "l2tp_nl_cmd_tunnel_get kprobe", prog8,
             trigger=trigger_nl, timeout=10)

    # ── Step 9: l2tp_ppp module available ────────────────────────────────────
    print(f"  Step  9: {'l2tp_ppp module available':52s}", end=" ")
    l2tp_ppp_ok = False
    try:
        with open("/proc/modules") as f:
            for line in f:
                if line.startswith("l2tp_ppp"):
                    l2tp_ppp_ok = True
                    break
    except OSError:
        pass
    if not l2tp_ppp_ok:
        r = subprocess.run(["modprobe", "--dry-run", "l2tp_ppp"],
                           capture_output=True, timeout=10)
        if r.returncode == 0:
            l2tp_ppp_ok = True
    if l2tp_ppp_ok:
        print(PASS)
        results.append((9, "l2tp_ppp available", PASS))
    else:
        print(SKIP)
        results.append((9, "l2tp_ppp available", SKIP))

    # ── Step 10: L2TP tracepoints in available_events ────────────────────────
    print(f"  Step 10: {'L2TP tracepoints in available_events':52s}", end=" ")
    tp_found = False
    try:
        with open("/sys/kernel/tracing/available_events") as f:
            for line in f:
                if "l2tp" in line.lower():
                    tp_found = True
                    break
    except OSError:
        pass
    if not tp_found:
        alt = "/sys/kernel/debug/tracing/available_events"
        try:
            with open(alt) as f:
                for line in f:
                    if "l2tp" in line.lower():
                        tp_found = True
                        break
        except OSError:
            pass
    if tp_found:
        print(PASS)
        results.append((10, "l2tp tracepoints", PASS))
    else:
        print(SKIP)
        results.append((10, "l2tp tracepoints", SKIP))


def print_summary():
    """Print final pass/fail/skip counts."""
    print()
    passed = sum(1 for _, _, s in results if s == PASS)
    failed = sum(1 for _, _, s in results if s == FAIL)
    skipped = sum(1 for _, _, s in results if s == SKIP)
    total = len(results)
    print(f"Results: {passed}/{total} passed, {failed} failed, {skipped} skipped")
    if failed > 0:
        sys.exit(1)


def main():
    print("\n=== L2TP (Layer 2 Tunneling Protocol) bpftrace verification ===\n")
    check_prereqs()
    run()
    print_summary()


if __name__ == "__main__":
    main()
