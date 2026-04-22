#!/usr/bin/env python3
"""
test_6lowpan_subsystem.py — bpftrace verification of 6LoWPAN.

Steps
-----
1.  Check 6lowpan module loaded
2.  Probe lowpan_register_netdevice    — 6LoWPAN interface registration
3.  Probe lowpan_header_compress       — IPHC compression (TX)
4.  Probe lowpan_header_decompress     — IPHC decompression (RX)
5.  Probe lowpan_nhc_add               — register NHC handler
6.  Probe lowpan_dev_debugfs_init      — debugfs entry creation
7.  Probe lowpan_is_addr_broadcast     — broadcast address check
8.  Check /sys/class/net for 6LoWPAN interfaces
9.  Check CONFIG_6LOWPAN in kernel config
10. Check /proc/net entries for 6lowpan
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


print("\n=== 6LoWPAN subsystem bpftrace verification ===\n")

# ── Step 1: 6lowpan module check ─────────────────────────────────────────────
print(f"  Step  1: {'6lowpan module loaded':50s}", end=" ")
lowpan_loaded = False
try:
    with open("/proc/modules") as f:
        for line in f:
            if line.startswith("6lowpan") or "ipv6_lowpan" in line:
                lowpan_loaded = True
                break
except OSError:
    pass

if not lowpan_loaded:
    r = subprocess.run(["modprobe", "6lowpan"], capture_output=True, timeout=10)
    if r.returncode == 0:
        lowpan_loaded = True

if lowpan_loaded:
    print(PASS)
    results.append((1, "6lowpan module", PASS))
else:
    # Check kallsyms as fallback (may be built-in)
    try:
        with open("/proc/kallsyms") as f:
            for line in f:
                if "lowpan_header_compress" in line:
                    lowpan_loaded = True
                    break
    except Exception:
        pass
    if lowpan_loaded:
        print(f"{PASS} (built-in)")
        results.append((1, "6lowpan module", PASS))
    else:
        print(SKIP)
        results.append((1, "6lowpan module", SKIP))

# ── Step 2: lowpan_register_netdevice ────────────────────────────────────────
prog2 = """
kprobe:lowpan_register_netdevice {
    printf("HIT lowpan_register_netdevice\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(2, "lowpan_register_netdevice kprobe", prog2, timeout=8)

# ── Step 3: lowpan_header_compress ───────────────────────────────────────────
prog3 = """
kprobe:lowpan_header_compress {
    printf("HIT lowpan_header_compress\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(3, "lowpan_header_compress kprobe", prog3, timeout=8)

# ── Step 4: lowpan_header_decompress ─────────────────────────────────────────
prog4 = """
kprobe:lowpan_header_decompress {
    printf("HIT lowpan_header_decompress\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(4, "lowpan_header_decompress kprobe", prog4, timeout=8)

# ── Step 5: lowpan_nhc_add ───────────────────────────────────────────────────
prog5 = """
kprobe:lowpan_nhc_add {
    printf("HIT lowpan_nhc_add\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(5, "lowpan_nhc_add kprobe", prog5, timeout=8)

# ── Step 6: lowpan_dev_debugfs_init ──────────────────────────────────────────
prog6 = """
kprobe:lowpan_dev_debugfs_init {
    printf("HIT lowpan_dev_debugfs_init\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(6, "lowpan_dev_debugfs_init kprobe", prog6, timeout=8)

# ── Step 7: lowpan_is_addr_broadcast ─────────────────────────────────────────
prog7 = """
kprobe:lowpan_is_addr_broadcast {
    printf("HIT lowpan_is_addr_broadcast\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(7, "lowpan_is_addr_broadcast kprobe", prog7, timeout=8)

# ── Step 8: 6LoWPAN interface in /sys/class/net ──────────────────────────────
print(f"  Step  8: {'6LoWPAN interface in /sys/class/net':50s}", end=" ")
lowpan_iface = None
try:
    for name in os.listdir("/sys/class/net"):
        # 6LoWPAN devices have ARPHRD_6LOWPAN (825)
        type_path = f"/sys/class/net/{name}/type"
        if os.path.exists(type_path):
            dev_type = open(type_path).read().strip()
            if dev_type == "825":
                lowpan_iface = name
                break
except OSError:
    pass

if lowpan_iface:
    print(f"{PASS} ({lowpan_iface})")
    results.append((8, "6LoWPAN interface", PASS))
else:
    print(SKIP)
    results.append((8, "6LoWPAN interface", SKIP))

# ── Step 9: CONFIG_6LOWPAN ───────────────────────────────────────────────────
print(f"  Step  9: {'CONFIG_6LOWPAN in kernel config':50s}", end=" ")
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
        if "CONFIG_6LOWPAN=y" in data or "CONFIG_6LOWPAN=m" in data:
            configured = True
            break
    except Exception:
        pass
if configured:
    print(PASS)
    results.append((9, "CONFIG_6LOWPAN", PASS))
else:
    print(SKIP)
    results.append((9, "CONFIG_6LOWPAN", SKIP))

# ── Step 10: /proc/net/6lowpan (debugfs) ─────────────────────────────────────
prog10 = """
tracepoint:syscalls:sys_enter_openat {
    if (str(args->filename) == "/sys/kernel/debug/6lowpan") {
        printf("HIT 6lowpan_debugfs\\n");
        exit();
    }
}
interval:s:3 { exit(); }
"""

def open_debugfs():
    try:
        os.listdir("/sys/kernel/debug/6lowpan")
    except Exception:
        pass

check(10, "6lowpan debugfs access", prog10,
      trigger=open_debugfs, expect="HIT", timeout=6)

# ── Summary ──────────────────────────────────────────────────────────────────
print()
passed = sum(1 for _, _, s in results if s == PASS)
failed = sum(1 for _, _, s in results if s == FAIL)
skipped = sum(1 for _, _, s in results if s == SKIP)
total = len(results)
print(f"Results: {passed}/{total} passed, {failed} failed, {skipped} skipped")
if failed > 0:
    sys.exit(1)
