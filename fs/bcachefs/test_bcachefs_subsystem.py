#!/usr/bin/env python3
"""
test_bcachefs_subsystem.py — bpftrace-based verification of bcachefs.

Steps
-----
1.  Probe bch2_write              — write path entry
2.  Probe bch2_read_folio         — read path entry
3.  Probe bch2_journal_write      — journal write
4.  Probe bch2_btree_iter_init    — B-tree cursor init
5.  Probe bch2_trans_commit       — B-tree transaction commit
6.  Probe bch2_alloc_sectors      — bucket allocation for writes
7.  Probe bch2_inode_create       — inode creation
8.  Probe bch2_fs_start            — filesystem mount
9.  Check /proc/fs/bcachefs        — procfs presence
10. Check sysfs /sys/fs/bcachefs   — sysfs presence
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


print("\n=== bcachefs subsystem bpftrace verification ===\n")

# Detect bcachefs mount
bcachefs_mount = None
try:
    with open("/proc/mounts") as f:
        for line in f:
            parts = line.split()
            if len(parts) >= 3 and parts[2] == "bcachefs":
                bcachefs_mount = parts[1]
                break
except OSError:
    pass

if bcachefs_mount:
    print(f"  [info] Found bcachefs mount at: {bcachefs_mount}\n")
else:
    print("  [info] No bcachefs mount found; I/O probes will SKIP if symbols absent.\n")


def io_trigger():
    mp = bcachefs_mount or "/tmp"
    try:
        p = os.path.join(mp, ".bcachefs_test_io")
        with open(p, "wb") as f:
            f.write(os.urandom(4096))
        with open(p, "rb") as f:
            f.read()
        os.unlink(p)
    except Exception:
        pass


# ── Step 1: bch2_write ───────────────────────────────────────────────────────
prog1 = """
kprobe:bch2_write {
    printf("HIT bch2_write\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(1, "bch2_write kprobe", prog1, trigger=io_trigger, timeout=10)

# ── Step 2: bch2_read_folio ──────────────────────────────────────────────────
prog2 = """
kprobe:bch2_read_folio {
    printf("HIT bch2_read_folio\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(2, "bch2_read_folio kprobe", prog2, trigger=io_trigger, timeout=10)

# ── Step 3: bch2_journal_write ───────────────────────────────────────────────
prog3 = """
kprobe:bch2_journal_write {
    printf("HIT bch2_journal_write\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(3, "bch2_journal_write kprobe", prog3, trigger=io_trigger, timeout=10)

# ── Step 4: bch2_btree_iter_init ─────────────────────────────────────────────
prog4 = """
kprobe:bch2_btree_iter_init {
    printf("HIT bch2_btree_iter_init\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(4, "bch2_btree_iter_init kprobe", prog4, trigger=io_trigger, timeout=10)

# ── Step 5: bch2_trans_commit ────────────────────────────────────────────────
prog5 = """
kprobe:bch2_trans_commit {
    printf("HIT bch2_trans_commit\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(5, "bch2_trans_commit kprobe", prog5, trigger=io_trigger, timeout=10)

# ── Step 6: bch2_alloc_sectors ───────────────────────────────────────────────
prog6 = """
kprobe:bch2_alloc_sectors {
    printf("HIT bch2_alloc_sectors\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(6, "bch2_alloc_sectors kprobe", prog6, trigger=io_trigger, timeout=10)

# ── Step 7: bch2_inode_create ────────────────────────────────────────────────
prog7 = """
kprobe:bch2_inode_create {
    printf("HIT bch2_inode_create\\n");
    exit();
}
interval:s:5 { exit(); }
"""

def create_file():
    mp = bcachefs_mount or "/tmp"
    p = os.path.join(mp, ".bcachefs_test_create")
    try:
        open(p, "w").close()
        os.unlink(p)
    except Exception:
        pass

check(7, "bch2_inode_create kprobe", prog7, trigger=create_file, timeout=10)

# ── Step 8: bch2_fs_start ────────────────────────────────────────────────────
prog8 = """
kprobe:bch2_fs_start {
    printf("HIT bch2_fs_start\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(8, "bch2_fs_start kprobe", prog8, timeout=8)

# ── Step 9: /proc/fs/bcachefs ────────────────────────────────────────────────
print(f"  Step  9: {'/proc/fs/bcachefs exists':48s}", end=" ")
if os.path.exists("/proc/fs/bcachefs"):
    print(PASS)
    results.append((9, "procfs bcachefs", PASS))
else:
    print(SKIP)
    results.append((9, "procfs bcachefs", SKIP))

# ── Step 10: /sys/fs/bcachefs ────────────────────────────────────────────────
print(f"  Step 10: {'/sys/fs/bcachefs exists':48s}", end=" ")
sysfs_paths = ["/sys/fs/bcachefs", "/sys/module/bcachefs"]
found = any(os.path.exists(p) for p in sysfs_paths)
if found:
    print(PASS)
    results.append((10, "sysfs bcachefs", PASS))
else:
    print(SKIP)
    results.append((10, "sysfs bcachefs", SKIP))

# ── Summary ──────────────────────────────────────────────────────────────────
print()
passed = sum(1 for _, _, s in results if s == PASS)
failed = sum(1 for _, _, s in results if s == FAIL)
skipped = sum(1 for _, _, s in results if s == SKIP)
total = len(results)
print(f"Results: {passed}/{total} passed, {failed} failed, {skipped} skipped")
if failed > 0:
    sys.exit(1)
