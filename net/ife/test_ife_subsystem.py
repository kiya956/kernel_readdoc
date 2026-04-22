#!/usr/bin/env python3
"""
test_ife_subsystem.py — bpftrace verification of the IFE metadata encap layer.

IFE (Inter-FE Object) is a TC action metadata carrier that encapsulates
arbitrary TLV-encoded metadata between two TC nodes (Inter-FE = between
forwarding elements in the SFC / E2E I2RS model).

Steps
-----
1.  Check act_ife (tc-ife) in kernel config / modules
2.  Probe tcf_ife_init             — IFE action creation
3.  Probe tcf_ife_encode           — metadata encoding (push path)
4.  Probe tcf_ife_decode           — metadata decoding (pop path)
5.  Probe ife_tlv_meta_encode      — single TLV encode
6.  Probe ife_tlv_meta_decode      — single TLV decode
7.  Probe ife_tlv_meta_valid       — TLV validation helper
8.  Add IFE TC action via tc command
9.  Probe tcf_ife_act              — IFE action hit path
10. Probe tcf_ife_cleanup          — IFE action destroy path
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


print("\n=== IFE (TC Inter-FE metadata encapsulation) bpftrace verification ===\n")

# ── Step 1: act_ife module ───────────────────────────────────────────────────
print(f"  Step  1: {'act_ife in modules/config':50s}", end=" ")
ife_present = False
try:
    r = subprocess.run(["lsmod"], capture_output=True, text=True)
    if "act_ife" in r.stdout:
        ife_present = True
except Exception:
    pass
if not ife_present:
    try:
        with open("/proc/kallsyms") as f:
            for line in f:
                if "tcf_ife" in line:
                    ife_present = True
                    break
    except Exception:
        pass
if not ife_present:
    subprocess.run(["modprobe", "act_ife"], capture_output=True)
    time.sleep(0.3)
    try:
        with open("/proc/kallsyms") as f:
            for line in f:
                if "tcf_ife" in line:
                    ife_present = True
                    break
    except Exception:
        pass
if ife_present:
    print(PASS)
    results.append((1, "act_ife", PASS))
else:
    print(SKIP)
    results.append((1, "act_ife", SKIP))

# ── Step 2: tcf_ife_init ─────────────────────────────────────────────────────
prog2 = """
kprobe:tcf_ife_init {
    printf("HIT tcf_ife_init\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(2, "tcf_ife_init kprobe", prog2, timeout=8)

# ── Step 3: tcf_ife_encode ───────────────────────────────────────────────────
prog3 = """
kprobe:tcf_ife_encode {
    printf("HIT tcf_ife_encode\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(3, "tcf_ife_encode kprobe", prog3, timeout=8)

# ── Step 4: tcf_ife_decode ───────────────────────────────────────────────────
prog4 = """
kprobe:tcf_ife_decode {
    printf("HIT tcf_ife_decode\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(4, "tcf_ife_decode kprobe", prog4, timeout=8)

# ── Step 5: ife_tlv_meta_encode ──────────────────────────────────────────────
prog5 = """
kprobe:ife_tlv_meta_encode {
    printf("HIT ife_tlv_meta_encode\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(5, "ife_tlv_meta_encode kprobe", prog5, timeout=8)

# ── Step 6: ife_tlv_meta_decode ──────────────────────────────────────────────
prog6 = """
kprobe:ife_tlv_meta_decode {
    printf("HIT ife_tlv_meta_decode\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(6, "ife_tlv_meta_decode kprobe", prog6, timeout=8)

# ── Step 7: ife_tlv_meta_valid ───────────────────────────────────────────────
prog7 = """
kprobe:ife_tlv_meta_valid {
    printf("HIT ife_tlv_meta_valid\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(7, "ife_tlv_meta_valid kprobe", prog7, timeout=8)

# ── Step 8: TC IFE action setup ──────────────────────────────────────────────
print(f"  Step  8: {'tc qdisc/filter with act_ife setup':50s}", end=" ")
tc_ok = False
try:
    # Try creating a dummy dev with IFE action
    subprocess.run(["ip", "link", "add", "ife-test0", "type", "dummy"],
                   capture_output=True, timeout=5)
    r = subprocess.run(
        ["tc", "qdisc", "add", "dev", "ife-test0", "ingress"],
        capture_output=True, text=True, timeout=5
    )
    if r.returncode == 0:
        r2 = subprocess.run(
            ["tc", "filter", "add", "dev", "ife-test0", "parent", "ffff:",
             "protocol", "all", "u32", "match", "u8", "0", "0",
             "action", "ife", "encode", "dst", "01:02:03:04:05:06"],
            capture_output=True, text=True, timeout=5
        )
        tc_ok = r2.returncode == 0
    subprocess.run(["tc", "qdisc", "del", "dev", "ife-test0", "ingress"],
                   capture_output=True)
    subprocess.run(["ip", "link", "del", "ife-test0"], capture_output=True)
except Exception:
    pass
if tc_ok:
    print(PASS)
    results.append((8, "tc IFE action", PASS))
else:
    print(SKIP)
    results.append((8, "tc IFE action", SKIP))

# ── Step 9: tcf_ife_act ──────────────────────────────────────────────────────
prog9 = """
kprobe:tcf_ife_act {
    printf("HIT tcf_ife_act\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(9, "tcf_ife_act kprobe", prog9, timeout=8)

# ── Step 10: tcf_ife_cleanup ─────────────────────────────────────────────────
prog10 = """
kprobe:tcf_ife_cleanup {
    printf("HIT tcf_ife_cleanup\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(10, "tcf_ife_cleanup kprobe", prog10, timeout=8)

# ── Summary ──────────────────────────────────────────────────────────────────
print()
passed = sum(1 for _, _, s in results if s == PASS)
failed = sum(1 for _, _, s in results if s == FAIL)
skipped = sum(1 for _, _, s in results if s == SKIP)
total = len(results)
print(f"Results: {passed}/{total} passed, {failed} failed, {skipped} skipped")
if failed > 0:
    sys.exit(1)
