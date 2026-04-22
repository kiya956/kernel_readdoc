#!/usr/bin/env python3
"""
test_exportfs_subsystem.py — bpftrace verification of the exportfs subsystem.

The exportfs layer provides the kernel's file-handle export API used by NFS.
It allows any filesystem to advertise "encode an inode into a file handle"
and "decode a file handle back to a dentry" via export_operations.

Steps
-----
1.  Check exportfs symbols in kallsyms
2.  Probe exportfs_encode_fh          — encode inode → file handle
3.  Probe exportfs_decode_fh          — decode file handle → dentry
4.  Probe exportfs_get_name           — get filename from inode
5.  Probe reconnect_path              — reconnect disconnected dentry chain
6.  Probe find_acceptable_alias       — find exportable dentry alias
7.  Check NFSD loaded (major exportfs user)
8.  Probe exportfs_encode_inode_fh   — encode inode file handle variant
9.  Probe generic_fh_to_parent       — generic parent FH lookup
10. Probe generic_fh_to_dentry        — generic FH→dentry lookup
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


print("\n=== exportfs (file-handle export API) bpftrace verification ===\n")

# ── Step 1: exportfs symbols ─────────────────────────────────────────────────
print(f"  Step  1: {'exportfs symbols in kallsyms':50s}", end=" ")
ksyms = False
try:
    with open("/proc/kallsyms") as f:
        for line in f:
            if "exportfs_encode_fh" in line or "exportfs_decode_fh" in line:
                ksyms = True
                break
except Exception:
    pass
if ksyms:
    print(PASS)
    results.append((1, "exportfs symbols", PASS))
else:
    print(SKIP)
    results.append((1, "exportfs symbols", SKIP))

# ── Step 2: exportfs_encode_fh ───────────────────────────────────────────────
prog2 = """
kprobe:exportfs_encode_fh {
    printf("HIT exportfs_encode_fh\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(2, "exportfs_encode_fh kprobe", prog2, timeout=8)

# ── Step 3: exportfs_decode_fh ───────────────────────────────────────────────
prog3 = """
kprobe:exportfs_decode_fh {
    printf("HIT exportfs_decode_fh\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(3, "exportfs_decode_fh kprobe", prog3, timeout=8)

# ── Step 4: exportfs_get_name ────────────────────────────────────────────────
prog4 = """
kprobe:exportfs_get_name {
    printf("HIT exportfs_get_name\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(4, "exportfs_get_name kprobe", prog4, timeout=8)

# ── Step 5: reconnect_path ───────────────────────────────────────────────────
prog5 = """
kprobe:reconnect_path {
    printf("HIT reconnect_path\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(5, "reconnect_path kprobe", prog5, timeout=8)

# ── Step 6: find_acceptable_alias ────────────────────────────────────────────
prog6 = """
kprobe:find_acceptable_alias {
    printf("HIT find_acceptable_alias\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(6, "find_acceptable_alias kprobe", prog6, timeout=8)

# ── Step 7: NFSD loaded ──────────────────────────────────────────────────────
print(f"  Step  7: {'nfsd module loaded or running':50s}", end=" ")
nfsd_present = False
try:
    r = subprocess.run(["lsmod"], capture_output=True, text=True)
    if "nfsd" in r.stdout:
        nfsd_present = True
except Exception:
    pass
if not nfsd_present:
    # Check if nfsd symbols are present in kallsyms
    try:
        with open("/proc/kallsyms") as f:
            for line in f:
                if " nfsd_" in line or "[nfsd]" in line:
                    nfsd_present = True
                    break
    except Exception:
        pass
if nfsd_present:
    print(PASS)
    results.append((7, "nfsd loaded", PASS))
else:
    print(SKIP)
    results.append((7, "nfsd loaded", SKIP))

# ── Step 8: exportfs_encode_inode_fh ─────────────────────────────────────────
prog8 = """
kprobe:exportfs_encode_inode_fh {
    printf("HIT exportfs_encode_inode_fh\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(8, "exportfs_encode_inode_fh kprobe", prog8, timeout=8)

# ── Step 9: generic_fh_to_parent ─────────────────────────────────────────────
prog9 = """
kprobe:generic_fh_to_parent {
    printf("HIT generic_fh_to_parent\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(9, "generic_fh_to_parent kprobe", prog9, timeout=8)

# ── Step 10: generic_fh_to_dentry ────────────────────────────────────────────
prog10 = """
kprobe:generic_fh_to_dentry {
    printf("HIT generic_fh_to_dentry\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(10, "generic_fh_to_dentry kprobe", prog10, timeout=8)

# ── Summary ──────────────────────────────────────────────────────────────────
print()
passed = sum(1 for _, _, s in results if s == PASS)
failed = sum(1 for _, _, s in results if s == FAIL)
skipped = sum(1 for _, _, s in results if s == SKIP)
total = len(results)
print(f"Results: {passed}/{total} passed, {failed} failed, {skipped} skipped")
if failed > 0:
    sys.exit(1)
