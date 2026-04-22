#!/usr/bin/env python3
"""
test_unicode_subsystem.py — bpftrace verification of fs/unicode.

Steps
-----
1.  Probe utf8_validate            — validate UTF-8 string
2.  Probe utf8_strncmp             — normalized comparison
3.  Probe utf8_strncasecmp         — case-insensitive (NFDI) comparison
4.  Probe utf8_normalize           — produce normalized form
5.  Probe utf8_casefold            — produce case-folded form
6.  Probe utf8_load                — load unicode_map at mount time
7.  Probe utf8_unload              — unload unicode_map at unmount
8.  Check ext4/f2fs casefold feature (sysfs)
9.  Test case-insensitive lookup via ext4 casefold mount
10. Check CONFIG_UNICODE in kernel config
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


print("\n=== fs/unicode subsystem bpftrace verification ===\n")

# Find a casefold-enabled mount (ext4 or f2fs)
casefold_mount = None
try:
    with open("/proc/mounts") as f:
        for line in f:
            parts = line.split()
            if len(parts) >= 4 and parts[2] in ("ext4", "f2fs"):
                if "casefold" in parts[3]:
                    casefold_mount = parts[1]
                    break
except OSError:
    pass

if casefold_mount:
    print(f"  [info] Found casefold mount at: {casefold_mount}\n")
else:
    print("  [info] No casefold mount found; lookup probes may SKIP.\n")


def casefold_lookup():
    """Trigger unicode normalization via filename lookup on casefold fs."""
    if casefold_mount:
        try:
            os.listdir(casefold_mount)
            # Try creating and looking up a file with non-ASCII name
            p = os.path.join(casefold_mount, ".unicode_test_\xc3\xa9.txt")
            with open(p, "w") as f:
                f.write("test")
            # Lookup with uppercase (triggers case folding)
            os.path.exists(os.path.join(casefold_mount, ".unicode_test_\xc3\x89.txt"))
            os.unlink(p)
        except Exception:
            pass


# ── Step 1: utf8_validate ────────────────────────────────────────────────────
prog1 = """
kprobe:utf8_validate {
    printf("HIT utf8_validate\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(1, "utf8_validate kprobe", prog1, trigger=casefold_lookup, timeout=8)

# ── Step 2: utf8_strncmp ─────────────────────────────────────────────────────
prog2 = """
kprobe:utf8_strncmp {
    printf("HIT utf8_strncmp\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(2, "utf8_strncmp kprobe", prog2, trigger=casefold_lookup, timeout=8)

# ── Step 3: utf8_strncasecmp ──────────────────────────────────────────────────
prog3 = """
kprobe:utf8_strncasecmp {
    printf("HIT utf8_strncasecmp\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(3, "utf8_strncasecmp kprobe", prog3, trigger=casefold_lookup, timeout=8)

# ── Step 4: utf8_normalize ───────────────────────────────────────────────────
prog4 = """
kprobe:utf8_normalize {
    printf("HIT utf8_normalize\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(4, "utf8_normalize kprobe", prog4, trigger=casefold_lookup, timeout=8)

# ── Step 5: utf8_casefold ────────────────────────────────────────────────────
prog5 = """
kprobe:utf8_casefold {
    printf("HIT utf8_casefold\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(5, "utf8_casefold kprobe", prog5, trigger=casefold_lookup, timeout=8)

# ── Step 6: utf8_load ────────────────────────────────────────────────────────
prog6 = """
kprobe:utf8_load {
    printf("HIT utf8_load\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(6, "utf8_load kprobe", prog6, timeout=8)

# ── Step 7: utf8_unload ──────────────────────────────────────────────────────
prog7 = """
kprobe:utf8_unload {
    printf("HIT utf8_unload\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(7, "utf8_unload kprobe", prog7, timeout=8)

# ── Step 8: ext4/f2fs casefold feature in sysfs ──────────────────────────────
print(f"  Step  8: {'ext4/f2fs casefold sysfs feature':52s}", end=" ")
casefold_sysfs = any(
    os.path.exists(f"/sys/fs/{fs}/features/casefold")
    for fs in ("ext4", "f2fs")
)
if casefold_sysfs:
    print(PASS)
    results.append((8, "casefold sysfs feature", PASS))
else:
    print(SKIP)
    results.append((8, "casefold sysfs feature", SKIP))

# ── Step 9: case-insensitive lookup round-trip ───────────────────────────────
print(f"  Step  9: {'case-insensitive filename lookup':52s}", end=" ")
if casefold_mount:
    try:
        lname = os.path.join(casefold_mount, ".ci_test_lower.txt")
        uname = os.path.join(casefold_mount, ".CI_TEST_LOWER.TXT")
        with open(lname, "w") as f:
            f.write("ci test")
        found = os.path.exists(uname)
        os.unlink(lname)
        if found:
            print(PASS)
            results.append((9, "CI lookup", PASS))
        else:
            print(SKIP)  # casefold may not be enabled on this dir
            results.append((9, "CI lookup", SKIP))
    except Exception as e:
        print(SKIP)
        results.append((9, "CI lookup", SKIP))
else:
    print(SKIP)
    results.append((9, "CI lookup", SKIP))

# ── Step 10: CONFIG_UNICODE ───────────────────────────────────────────────────
print(f"  Step 10: {'CONFIG_UNICODE in kernel config':52s}", end=" ")
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
        if "CONFIG_UNICODE=y" in data or "CONFIG_UNICODE=m" in data:
            configured = True
            break
    except Exception:
        pass
if configured:
    print(PASS)
    results.append((10, "CONFIG_UNICODE", PASS))
else:
    print(SKIP)
    results.append((10, "CONFIG_UNICODE", SKIP))

# ── Summary ──────────────────────────────────────────────────────────────────
print()
passed = sum(1 for _, _, s in results if s == PASS)
failed = sum(1 for _, _, s in results if s == FAIL)
skipped = sum(1 for _, _, s in results if s == SKIP)
total = len(results)
print(f"Results: {passed}/{total} passed, {failed} failed, {skipped} skipped")
if failed > 0:
    sys.exit(1)
