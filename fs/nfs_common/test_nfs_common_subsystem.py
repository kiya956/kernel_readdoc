#!/usr/bin/env python3
"""
test_nfs_common_subsystem.py — bpftrace verification of fs/nfs_common.

The nfs_common module provides shared grace period management, NFS ACL
encoding, server-side copy helpers, and local I/O fast path used by
both the NFS client and server.

Steps
-----
1.  Check nfs_common symbols in kallsyms
2.  Probe locks_start_grace         — begin grace period
3.  Probe locks_end_grace           — end grace period
4.  Probe locks_in_grace            — query grace state
5.  Probe locks_block_opens         — block opens during grace
6.  Probe nfsacl_encode             — POSIX ACL → NFS3 wire
7.  Probe nfsacl_decode             — NFS3 wire → POSIX ACL
8.  Check nfsd running (primary grace period user)
9.  Probe nfs_ssc_register_ops      — server-side copy registration
10. Probe nfs_localio_enable_server — local I/O fast-path enable
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


print("\n=== nfs_common (NFS shared utilities) bpftrace verification ===\n")

# ── Step 1: nfs_common symbols ───────────────────────────────────────────────
print(f"  Step  1: {'locks_start_grace in kallsyms':52s}", end=" ")
sym_ok = False
try:
    with open("/proc/kallsyms") as f:
        for line in f:
            if "locks_start_grace" in line or "locks_end_grace" in line:
                sym_ok = True
                break
except Exception:
    pass
if sym_ok:
    print(PASS)
    results.append((1, "nfs_common symbols", PASS))
else:
    print(SKIP)
    results.append((1, "nfs_common symbols", SKIP))

# ── Step 2: locks_start_grace ────────────────────────────────────────────────
prog2 = """
kprobe:locks_start_grace {
    printf("HIT locks_start_grace\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(2, "locks_start_grace kprobe", prog2, timeout=8)

# ── Step 3: locks_end_grace ──────────────────────────────────────────────────
prog3 = """
kprobe:locks_end_grace {
    printf("HIT locks_end_grace\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(3, "locks_end_grace kprobe", prog3, timeout=8)

# ── Step 4: locks_in_grace ───────────────────────────────────────────────────
prog4 = """
kprobe:locks_in_grace {
    printf("HIT locks_in_grace\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(4, "locks_in_grace kprobe", prog4, timeout=8)

# ── Step 5: locks_block_opens ────────────────────────────────────────────────
prog5 = """
kprobe:locks_block_opens {
    printf("HIT locks_block_opens\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(5, "locks_block_opens kprobe", prog5, timeout=8)

# ── Step 6: nfsacl_encode ────────────────────────────────────────────────────
prog6 = """
kprobe:nfsacl_encode {
    printf("HIT nfsacl_encode\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(6, "nfsacl_encode kprobe", prog6, timeout=8)

# ── Step 7: nfsacl_decode ────────────────────────────────────────────────────
prog7 = """
kprobe:nfsacl_decode {
    printf("HIT nfsacl_decode\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(7, "nfsacl_decode kprobe", prog7, timeout=8)

# ── Step 8: nfsd running ─────────────────────────────────────────────────────
print(f"  Step  8: {'nfsd module/service present':52s}", end=" ")
nfsd_ok = False
try:
    r = subprocess.run(["lsmod"], capture_output=True, text=True)
    if "nfsd" in r.stdout:
        nfsd_ok = True
except Exception:
    pass
if not nfsd_ok:
    try:
        with open("/proc/kallsyms") as f:
            for line in f:
                if "[nfsd]" in line or "nfsd_dispatch" in line:
                    nfsd_ok = True
                    break
    except Exception:
        pass
if nfsd_ok:
    print(PASS)
    results.append((8, "nfsd present", PASS))
else:
    print(SKIP)
    results.append((8, "nfsd present", SKIP))

# ── Step 9: nfs_ssc_register_ops ─────────────────────────────────────────────
prog9 = """
kprobe:nfs_ssc_register_ops {
    printf("HIT nfs_ssc_register_ops\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(9, "nfs_ssc_register_ops kprobe", prog9, timeout=8)

# ── Step 10: nfs_localio_enable_server ───────────────────────────────────────
prog10 = """
kprobe:nfs_localio_enable_server {
    printf("HIT nfs_localio_enable_server\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(10, "nfs_localio_enable_server kprobe", prog10, timeout=8)

# ── Summary ──────────────────────────────────────────────────────────────────
print()
passed = sum(1 for _, _, s in results if s == PASS)
failed = sum(1 for _, _, s in results if s == FAIL)
skipped = sum(1 for _, _, s in results if s == SKIP)
total = len(results)
print(f"Results: {passed}/{total} passed, {failed} failed, {skipped} skipped")
if failed > 0:
    sys.exit(1)
