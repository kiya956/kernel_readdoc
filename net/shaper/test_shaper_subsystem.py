#!/usr/bin/env python3
"""
test_shaper_subsystem.py — bpftrace verification of net/shaper.

The kernel net_shaper infrastructure provides hardware-offloaded network
bandwidth shaping via Generic Netlink, targeting NIC hardware rate limiters.

Steps
-----
1.  Check NET_SHAPER Netlink family registered
2.  Probe net_shaper_nl_set_doit     — create/update shaper via netlink
3.  Probe net_shaper_nl_get_doit     — read shaper config
4.  Probe net_shaper_nl_delete_doit  — remove shaper
5.  Probe net_shaper_lock            — hierarchy lock
6.  Probe net_shaper_unlock          — hierarchy unlock
7.  Probe net_shaper_hierarchy       — hierarchy lookup
8.  Check net_shaper symbols in kallsyms
9.  Probe net_shaper_nl_group_doit   — atomic group update
10. Probe net_shaper_hierarchy_setup — per-netdev hierarchy init
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


print("\n=== net/shaper (network bandwidth shaper) bpftrace verification ===\n")

# ── Step 1: NET_SHAPER family ────────────────────────────────────────────────
print(f"  Step  1: {'net_shaper family in /proc/net/protocols or kallsyms':52s}", end=" ")
shaper_present = False
try:
    with open("/proc/kallsyms") as f:
        for line in f:
            if "net_shaper_nl" in line or "net_shaper_set" in line:
                shaper_present = True
                break
except Exception:
    pass
if not shaper_present:
    try:
        r = subprocess.run(
            ["grep", "-r", "net_shaper", "/proc/net/"],
            capture_output=True, text=True, timeout=5
        )
        if "net_shaper" in r.stdout:
            shaper_present = True
    except Exception:
        pass
if shaper_present:
    print(PASS)
    results.append((1, "net_shaper present", PASS))
else:
    print(SKIP)
    results.append((1, "net_shaper present", SKIP))

# ── Step 2: net_shaper_nl_set_doit ───────────────────────────────────────────
prog2 = """
kprobe:net_shaper_nl_set_doit {
    printf("HIT net_shaper_nl_set_doit\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(2, "net_shaper_nl_set_doit kprobe", prog2, timeout=8)

# ── Step 3: net_shaper_nl_get_doit ───────────────────────────────────────────
prog3 = """
kprobe:net_shaper_nl_get_doit {
    printf("HIT net_shaper_nl_get_doit\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(3, "net_shaper_nl_get_doit kprobe", prog3, timeout=8)

# ── Step 4: net_shaper_nl_delete_doit ────────────────────────────────────────
prog4 = """
kprobe:net_shaper_nl_delete_doit {
    printf("HIT net_shaper_nl_delete_doit\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(4, "net_shaper_nl_delete_doit kprobe", prog4, timeout=8)

# ── Step 5: net_shaper_lock ───────────────────────────────────────────────────
prog5 = """
kprobe:net_shaper_lock {
    printf("HIT net_shaper_lock\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(5, "net_shaper_lock kprobe", prog5, timeout=8)

# ── Step 6: net_shaper_unlock ────────────────────────────────────────────────
prog6 = """
kprobe:net_shaper_unlock {
    printf("HIT net_shaper_unlock\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(6, "net_shaper_unlock kprobe", prog6, timeout=8)

# ── Step 7: net_shaper_hierarchy ─────────────────────────────────────────────
prog7 = """
kprobe:net_shaper_hierarchy {
    printf("HIT net_shaper_hierarchy\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(7, "net_shaper_hierarchy kprobe", prog7, timeout=8)

# ── Step 8: kallsyms check ───────────────────────────────────────────────────
print(f"  Step  8: {'net_shaper symbols in kallsyms':52s}", end=" ")
ksym_ok = False
symbols_to_check = ["net_shaper_nl_set_doit", "net_shaper_hierarchy_setup",
                    "net_shaper_nl_group_doit"]
try:
    with open("/proc/kallsyms") as f:
        content = f.read()
    for sym in symbols_to_check:
        if sym in content:
            ksym_ok = True
            break
except Exception:
    pass
if ksym_ok:
    print(PASS)
    results.append((8, "net_shaper kallsyms", PASS))
else:
    print(SKIP)
    results.append((8, "net_shaper kallsyms", SKIP))

# ── Step 9: net_shaper_nl_group_doit ─────────────────────────────────────────
prog9 = """
kprobe:net_shaper_nl_group_doit {
    printf("HIT net_shaper_nl_group_doit\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(9, "net_shaper_nl_group_doit kprobe", prog9, timeout=8)

# ── Step 10: net_shaper_hierarchy_setup ──────────────────────────────────────
prog10 = """
kprobe:net_shaper_hierarchy_setup {
    printf("HIT net_shaper_hierarchy_setup\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(10, "net_shaper_hierarchy_setup kprobe", prog10, timeout=8)

# ── Summary ──────────────────────────────────────────────────────────────────
print()
passed = sum(1 for _, _, s in results if s == PASS)
failed = sum(1 for _, _, s in results if s == FAIL)
skipped = sum(1 for _, _, s in results if s == SKIP)
total = len(results)
print(f"Results: {passed}/{total} passed, {failed} failed, {skipped} skipped")
if failed > 0:
    sys.exit(1)
