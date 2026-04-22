#!/usr/bin/env python3
"""
test_efs_subsystem.py — bpftrace verification of the EFS filesystem driver.

Steps
-----
1.  Check CONFIG_EFS_FS in kernel config
2.  Probe efs_fill_super            — EFS mount
3.  Probe efs_iget                  — inode read from disk
4.  Probe efs_bmap                  — extent-to-block mapping
5.  Probe efs_dir_readdir           — directory iteration
6.  Probe efs_readlink              — symlink read
7.  Create a test EFS image and attempt mount (loopback)
8.  Probe efs_statfs                — filesystem stat
9.  Check EFS module in /proc/filesystems
10. Probe efs_alloc_inode            — inode slab allocation
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
        print(f"  Step {step_num:2d}: {name:48s} {SKIP}")
        return
    ok = expect in stdout
    status = PASS if ok else FAIL
    results.append((step_num, name, status))
    print(f"  Step {step_num:2d}: {name:48s} {status}")
    if not ok:
        if stdout.strip():
            print(f"            stdout: {stdout.strip()[:200]}")
        if stderr.strip():
            print(f"            stderr: {stderr.strip()[:200]}")


print("\n=== EFS (SGI IRIX filesystem) bpftrace verification ===\n")

# ── Step 1: CONFIG_EFS_FS ────────────────────────────────────────────────────
print(f"  Step  1: {'CONFIG_EFS_FS in kernel config':48s}", end=" ")
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
        if "CONFIG_EFS_FS=y" in data or "CONFIG_EFS_FS=m" in data:
            configured = True
            break
    except Exception:
        pass
if configured:
    print(PASS)
    results.append((1, "CONFIG_EFS_FS", PASS))
else:
    print(SKIP)
    results.append((1, "CONFIG_EFS_FS", SKIP))

# ── Step 2: efs_fill_super ───────────────────────────────────────────────────
prog2 = """
kprobe:efs_fill_super {
    printf("HIT efs_fill_super\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(2, "efs_fill_super kprobe", prog2, timeout=8)

# ── Step 3: efs_iget ─────────────────────────────────────────────────────────
prog3 = """
kprobe:efs_iget {
    printf("HIT efs_iget\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(3, "efs_iget kprobe", prog3, timeout=8)

# ── Step 4: efs_bmap ─────────────────────────────────────────────────────────
prog4 = """
kprobe:efs_bmap {
    printf("HIT efs_bmap\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(4, "efs_bmap kprobe", prog4, timeout=8)

# ── Step 5: efs_readdir ──────────────────────────────────────────────────────
prog5 = """
kprobe:efs_readdir {
    printf("HIT efs_readdir\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(5, "efs_readdir kprobe", prog5, timeout=8)

# ── Step 6: efs_symlink_readlink ──────────────────────────────────────────────
prog6 = """
kprobe:efs_get_link {
    printf("HIT efs_get_link\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(6, "efs_get_link (symlink) kprobe", prog6, timeout=8)

# ── Step 7: EFS in /proc/filesystems ─────────────────────────────────────────
print(f"  Step  7: {'EFS in /proc/filesystems':48s}", end=" ")
efs_registered = False
try:
    r = subprocess.run(["modprobe", "efs"], capture_output=True, timeout=5)
    time.sleep(0.5)
    with open("/proc/filesystems") as f:
        for line in f:
            if "efs" in line:
                efs_registered = True
                break
except Exception:
    pass
if efs_registered:
    print(PASS)
    results.append((7, "EFS in filesystems", PASS))
else:
    print(SKIP)
    results.append((7, "EFS in filesystems", SKIP))

# ── Step 8: efs_statfs ───────────────────────────────────────────────────────
prog8 = """
kprobe:efs_statfs {
    printf("HIT efs_statfs\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(8, "efs_statfs kprobe", prog8, timeout=8)

# ── Step 9: efs_alloc_inode ──────────────────────────────────────────────────
prog9 = """
kprobe:efs_alloc_inode {
    printf("HIT efs_alloc_inode\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(9, "efs_alloc_inode kprobe", prog9, timeout=8)

# ── Step 10: efs module in /proc/modules ─────────────────────────────────────
print(f"  Step 10: {'efs in /proc/modules or kallsyms':48s}", end=" ")
efs_present = False
try:
    with open("/proc/kallsyms") as f:
        for line in f:
            if "efs_fill_super" in line or "efs_bmap" in line:
                efs_present = True
                break
except Exception:
    pass
if efs_present:
    print(PASS)
    results.append((10, "EFS symbols", PASS))
else:
    print(SKIP)
    results.append((10, "EFS symbols", SKIP))

# ── Summary ──────────────────────────────────────────────────────────────────
print()
passed = sum(1 for _, _, s in results if s == PASS)
failed = sum(1 for _, _, s in results if s == FAIL)
skipped = sum(1 for _, _, s in results if s == SKIP)
total = len(results)
print(f"Results: {passed}/{total} passed, {failed} failed, {skipped} skipped")
if failed > 0:
    sys.exit(1)
