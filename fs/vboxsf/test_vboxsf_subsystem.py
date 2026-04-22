#!/usr/bin/env python3
"""
test_vboxsf_subsystem.py — bpftrace verification of the vboxsf driver.

vboxsf mounts VirtualBox host-shared directories inside a Linux guest via
the VBoxGuest HGCM (Host-Guest Communication Manager) protocol.

Steps
-----
1.  Check vboxsf module loaded
2.  Probe vboxsf_fill_super         — VBoxSF mount
3.  Probe vboxsf_inode_create       — file creation via HGCM
4.  Probe vboxsf_dir_lookup         — directory entry lookup
5.  Probe vboxsf_file_read_iter     — file read via HGCM
6.  Probe vboxsf_file_write_iter    — file write via HGCM
7.  Check /proc/filesystems for vboxsf
8.  Probe vboxsf_readdir            — directory listing
9.  Probe vboxsf_release            — file handle close
10. Probe vboxsf_unlink             — file deletion
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


print("\n=== vboxsf (VirtualBox Shared Folders) bpftrace verification ===\n")
print("  NOTE: Most steps SKIP on non-VirtualBox environments.\n")

# ── Step 1: vboxsf module ────────────────────────────────────────────────────
print(f"  Step  1: {'vboxsf module loaded':50s}", end=" ")
vboxsf_present = False
try:
    r = subprocess.run(["lsmod"], capture_output=True, text=True)
    if "vboxsf" in r.stdout:
        vboxsf_present = True
except Exception:
    pass
if not vboxsf_present:
    subprocess.run(["modprobe", "vboxsf"], capture_output=True)
    time.sleep(0.3)
    try:
        with open("/proc/kallsyms") as f:
            for line in f:
                if "vboxsf_fill_super" in line:
                    vboxsf_present = True
                    break
    except Exception:
        pass
if vboxsf_present:
    print(PASS)
    results.append((1, "vboxsf loaded", PASS))
else:
    print(SKIP)
    results.append((1, "vboxsf loaded", SKIP))

# ── Step 2: vboxsf_fill_super ────────────────────────────────────────────────
prog2 = """
kprobe:vboxsf_fill_super {
    printf("HIT vboxsf_fill_super\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(2, "vboxsf_fill_super kprobe", prog2, timeout=8)

# ── Step 3: vboxsf_inode_create ──────────────────────────────────────────────
prog3 = """
kprobe:vboxsf_inode_create {
    printf("HIT vboxsf_inode_create\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(3, "vboxsf_inode_create kprobe", prog3, timeout=8)

# ── Step 4: vboxsf_dir_lookup ────────────────────────────────────────────────
prog4 = """
kprobe:vboxsf_dir_lookup {
    printf("HIT vboxsf_dir_lookup\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(4, "vboxsf_dir_lookup kprobe", prog4, timeout=8)

# ── Step 5: vboxsf_file_read_iter ────────────────────────────────────────────
prog5 = """
kprobe:vboxsf_file_read_iter {
    printf("HIT vboxsf_file_read_iter\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(5, "vboxsf_file_read_iter kprobe", prog5, timeout=8)

# ── Step 6: vboxsf_file_write_iter ───────────────────────────────────────────
prog6 = """
kprobe:vboxsf_file_write_iter {
    printf("HIT vboxsf_file_write_iter\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(6, "vboxsf_file_write_iter kprobe", prog6, timeout=8)

# ── Step 7: /proc/filesystems ────────────────────────────────────────────────
print(f"  Step  7: {'vboxsf in /proc/filesystems':50s}", end=" ")
fs_ok = False
try:
    with open("/proc/filesystems") as f:
        for line in f:
            if "vboxsf" in line:
                fs_ok = True
                break
except Exception:
    pass
if fs_ok:
    print(PASS)
    results.append((7, "vboxsf in filesystems", PASS))
else:
    print(SKIP)
    results.append((7, "vboxsf in filesystems", SKIP))

# ── Step 8: vboxsf_readdir ───────────────────────────────────────────────────
prog8 = """
kprobe:vboxsf_readdir {
    printf("HIT vboxsf_readdir\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(8, "vboxsf_readdir kprobe", prog8, timeout=8)

# ── Step 9: vboxsf_release ───────────────────────────────────────────────────
prog9 = """
kprobe:vboxsf_release {
    printf("HIT vboxsf_release\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(9, "vboxsf_release kprobe", prog9, timeout=8)

# ── Step 10: vboxsf_unlink ───────────────────────────────────────────────────
prog10 = """
kprobe:vboxsf_unlink {
    printf("HIT vboxsf_unlink\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(10, "vboxsf_unlink kprobe", prog10, timeout=8)

# ── Summary ──────────────────────────────────────────────────────────────────
print()
passed = sum(1 for _, _, s in results if s == PASS)
failed = sum(1 for _, _, s in results if s == FAIL)
skipped = sum(1 for _, _, s in results if s == SKIP)
total = len(results)
print(f"Results: {passed}/{total} passed, {failed} failed, {skipped} skipped")
if failed > 0:
    sys.exit(1)
