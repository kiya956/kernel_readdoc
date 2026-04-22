#!/usr/bin/env python3
"""
test_fsverity_subsystem.py — bpftrace-based verification of fs-verity.

Steps
-----
1.  Probe fsverity_file_open           — open of a verity-protected file
2.  Probe fsverity_verify_blocks       — per-block Merkle verify (hot path)
3.  Probe fsverity_ioctl_enable        — FS_IOC_ENABLE_VERITY ioctl
4.  Probe fsverity_ioctl_measure       — FS_IOC_MEASURE_VERITY ioctl
5.  Probe fsverity_prepare_merkle_tree_block — Merkle tree build during enable
6.  Probe fsverity_hash_block          — block hashing during tree build
7.  Probe fsverity_verify_page         — legacy per-page verify
8.  Check /sys/fs/<fstype>/features/verity — sysfs feature flag
9.  Verify kernel config CONFIG_FS_VERITY
10. Probe fsverity_get_digest          — digest retrieval
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


print("\n=== fs-verity subsystem bpftrace verification ===\n")

# Detect a verity-capable mount (ext4/f2fs/btrfs/erofs)
VERITY_FSTYPES = {"ext4", "f2fs", "btrfs", "erofs"}
verity_mount = None
try:
    with open("/proc/mounts") as f:
        for line in f:
            parts = line.split()
            if len(parts) >= 3 and parts[2] in VERITY_FSTYPES:
                verity_mount = (parts[1], parts[2])
                break
except OSError:
    pass

if verity_mount:
    print(f"  [info] Found verity-capable mount: {verity_mount[0]} ({verity_mount[1]})\n")
else:
    print("  [info] No verity-capable mount found; file-open/read steps may SKIP.\n")


def open_files_trigger():
    if not verity_mount:
        return
    mp = verity_mount[0]
    try:
        entries = os.listdir(mp)
        for name in entries[:5]:
            p = os.path.join(mp, name)
            if os.path.isfile(p):
                try:
                    with open(p, "rb") as fh:
                        fh.read(4096)
                except OSError:
                    pass
    except OSError:
        pass


# ── Step 1: fsverity_file_open ───────────────────────────────────────────────
prog1 = """
kprobe:fsverity_file_open {
    printf("HIT fsverity_file_open\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(1, "fsverity_file_open kprobe", prog1, trigger=open_files_trigger, timeout=8)

# ── Step 2: fsverity_verify_blocks ──────────────────────────────────────────
prog2 = """
kprobe:fsverity_verify_blocks {
    printf("HIT fsverity_verify_blocks\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(2, "fsverity_verify_blocks kprobe", prog2, trigger=open_files_trigger, timeout=8)

# ── Step 3: fsverity_ioctl_enable ───────────────────────────────────────────
prog3 = """
kprobe:fsverity_ioctl_enable {
    printf("HIT fsverity_ioctl_enable\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(3, "fsverity_ioctl_enable kprobe", prog3, timeout=8)

# ── Step 4: fsverity_ioctl_measure ──────────────────────────────────────────
prog4 = """
kprobe:fsverity_ioctl_measure {
    printf("HIT fsverity_ioctl_measure\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(4, "fsverity_ioctl_measure kprobe", prog4, timeout=8)

# ── Step 5: fsverity_prepare_merkle_tree_block ──────────────────────────────
prog5 = """
kprobe:fsverity_prepare_merkle_tree_block {
    printf("HIT fsverity_prepare_merkle_tree_block\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(5, "fsverity_prepare_merkle_tree_block kprobe", prog5, timeout=8)

# ── Step 6: fsverity_hash_block ─────────────────────────────────────────────
prog6 = """
kprobe:fsverity_hash_block {
    printf("HIT fsverity_hash_block\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(6, "fsverity_hash_block kprobe", prog6, timeout=8)

# ── Step 7: fsverity_verify_page (older alias) ──────────────────────────────
prog7 = """
kprobe:fsverity_verify_page {
    printf("HIT fsverity_verify_page\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(7, "fsverity_verify_page kprobe", prog7, trigger=open_files_trigger, timeout=8)

# ── Step 8: sysfs verity feature flag ───────────────────────────────────────
print(f"  Step  8: {'sysfs verity feature flag':52s}", end=" ")
sysfs_paths = []
for fstype in VERITY_FSTYPES:
    sysfs_paths.append(f"/sys/fs/{fstype}/features/verity")
found = any(os.path.exists(p) for p in sysfs_paths)
if found:
    print(PASS)
    results.append((8, "sysfs verity flag", PASS))
else:
    print(SKIP)
    results.append((8, "sysfs verity flag", SKIP))

# ── Step 9: CONFIG_FS_VERITY ─────────────────────────────────────────────────
print(f"  Step  9: {'CONFIG_FS_VERITY in kernel config':52s}", end=" ")
config_files = ["/proc/config.gz", "/boot/config-" + os.uname().release]
verity_configured = False
for cf in config_files:
    if not os.path.exists(cf):
        continue
    try:
        if cf.endswith(".gz"):
            import gzip
            data = gzip.open(cf, "rt").read()
        else:
            data = open(cf).read()
        if "CONFIG_FS_VERITY=y" in data or "CONFIG_FS_VERITY=m" in data:
            verity_configured = True
            break
    except Exception:
        pass
if verity_configured:
    print(PASS)
    results.append((9, "CONFIG_FS_VERITY", PASS))
else:
    print(SKIP)
    results.append((9, "CONFIG_FS_VERITY", SKIP))

# ── Step 10: fsverity_get_digest ─────────────────────────────────────────────
prog10 = """
kprobe:fsverity_get_digest {
    printf("HIT fsverity_get_digest\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(10, "fsverity_get_digest kprobe", prog10, timeout=8)

# ── Summary ──────────────────────────────────────────────────────────────────
print()
passed = sum(1 for _, _, s in results if s == PASS)
failed = sum(1 for _, _, s in results if s == FAIL)
skipped = sum(1 for _, _, s in results if s == SKIP)
total = len(results)
print(f"Results: {passed}/{total} passed, {failed} failed, {skipped} skipped")
if failed > 0:
    sys.exit(1)
