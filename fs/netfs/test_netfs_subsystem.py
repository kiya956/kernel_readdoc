#!/usr/bin/env python3
"""
test_netfs_subsystem.py — bpftrace-based verification of the netfs library.

Each step attaches a short bpftrace program to a kernel probe, triggers
a workload, then checks whether the probe fired.

Requirements:
  - Root privileges
  - bpftrace >= 0.14
  - A filesystem that uses the netfs library (e.g., nfs, cifs, ceph, erofs)
    mounted somewhere, OR the dummy nbd device.

Steps
-----
1.  Probe netfs_read_folio        — single-page read entry point
2.  Probe netfs_readahead         — readahead entry point
3.  Probe netfs_write_begin       — buffered-write begin
4.  Probe netfs_write_end         — buffered-write end
5.  Probe fscache_acquire_volume  — fscache volume (superblock cookie) creation
6.  Probe fscache_acquire_cookie  — fscache per-inode cookie creation
7.  Probe netfs_free_request      — netfs_io_request lifecycle end
8.  Check /proc/fs/fscache/stats  — fscache stats file exists
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
ATTACH_WAIT = 2.0   # seconds to let probes attach before triggering


def run_bpftrace(program: str, trigger=None, timeout: int = 10) -> tuple[str, str, bool]:
    """Run bpftrace program, optionally execute trigger(), return (stdout, stderr, skipped)."""
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


def find_netfs_mount() -> str | None:
    """Return a mount point using a netfs-backed filesystem, or None."""
    netfs_fstypes = {"nfs", "nfs4", "cifs", "ceph", "afs", "9p", "erofs"}
    try:
        with open("/proc/mounts") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 3 and parts[2] in netfs_fstypes:
                    return parts[1]
    except OSError:
        pass
    return None


def trigger_read(path: str):
    """Read a file to trigger readahead / read_folio."""
    try:
        files = [os.path.join(path, n) for n in os.listdir(path) if not n.startswith(".")]
        for f in files[:3]:
            if os.path.isfile(f):
                with open(f, "rb") as fh:
                    fh.read(65536)
                return
    except Exception:
        pass


def trigger_write(path: str):
    """Write a small file to trigger write_begin / write_end."""
    try:
        tmp = os.path.join(path, ".kmsan_test_write")
        with open(tmp, "w") as fh:
            fh.write("netfs write test\n")
        os.unlink(tmp)
    except Exception:
        pass


results = []


def check(step_num: int, name: str, program: str, trigger=None,
          expect: str = "HIT", timeout: int = 10):
    stdout, stderr, skipped = run_bpftrace(program, trigger, timeout)
    if skipped:
        results.append((step_num, name, SKIP))
        print(f"  Step {step_num:2d}: {name:45s} {SKIP}")
        return
    ok = expect in stdout
    status = PASS if ok else FAIL
    results.append((step_num, name, status))
    print(f"  Step {step_num:2d}: {name:45s} {status}")
    if not ok:
        if stdout.strip():
            print(f"            stdout: {stdout.strip()[:200]}")
        if stderr.strip():
            print(f"            stderr: {stderr.strip()[:200]}")


print("\n=== netfs subsystem bpftrace verification ===\n")

mount = find_netfs_mount()
if mount:
    print(f"  [info] Found netfs-backed mount at: {mount}\n")
else:
    print("  [info] No netfs-backed mount found; read/write steps will SKIP if probes absent.\n")

# ── Step 1: netfs_read_folio ─────────────────────────────────────────────────
prog1 = """
kprobe:netfs_read_folio {
    printf("HIT netfs_read_folio inode=%p\\n", arg0);
    exit();
}
interval:s:5 { exit(); }
"""
check(1, "netfs_read_folio kprobe",
      prog1,
      trigger=(lambda: trigger_read(mount)) if mount else None,
      timeout=8)

# ── Step 2: netfs_readahead ──────────────────────────────────────────────────
prog2 = """
kprobe:netfs_readahead {
    printf("HIT netfs_readahead\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(2, "netfs_readahead kprobe",
      prog2,
      trigger=(lambda: trigger_read(mount)) if mount else None,
      timeout=8)

# ── Step 3: netfs_write_begin ────────────────────────────────────────────────
prog3 = """
kprobe:netfs_write_begin {
    printf("HIT netfs_write_begin\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(3, "netfs_write_begin kprobe",
      prog3,
      trigger=(lambda: trigger_write(mount)) if mount else None,
      timeout=8)

# ── Step 4: netfs_write_end ──────────────────────────────────────────────────
prog4 = """
kprobe:netfs_write_end {
    printf("HIT netfs_write_end\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(4, "netfs_write_end kprobe",
      prog4,
      trigger=(lambda: trigger_write(mount)) if mount else None,
      timeout=8)

# ── Step 5: fscache_acquire_volume ───────────────────────────────────────────
prog5 = """
kprobe:fscache_acquire_volume {
    printf("HIT fscache_acquire_volume\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(5, "fscache_acquire_volume kprobe", prog5, timeout=8)

# ── Step 6: fscache_acquire_cookie ───────────────────────────────────────────
prog6 = """
kprobe:fscache_acquire_cookie {
    printf("HIT fscache_acquire_cookie\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(6, "fscache_acquire_cookie kprobe", prog6, timeout=8)

# ── Step 7: netfs_free_request ───────────────────────────────────────────────
prog7 = """
kprobe:netfs_free_request {
    printf("HIT netfs_free_request\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(7, "netfs_free_request kprobe",
      prog7,
      trigger=(lambda: trigger_read(mount)) if mount else None,
      timeout=8)

# ── Step 8: /proc/fs/fscache/stats ──────────────────────────────────────────
print(f"  Step  8: {'fscache /proc stats file exists':45s}", end=" ")
stats_paths = ["/proc/fs/fscache/stats", "/sys/kernel/debug/fscache/stats"]
found_stats = any(os.path.exists(p) for p in stats_paths)
if found_stats:
    print(PASS)
    results.append((8, "fscache stats file", PASS))
else:
    # fscache may be compiled as module and not yet loaded
    print(SKIP)
    results.append((8, "fscache stats file", SKIP))

# ── Summary ──────────────────────────────────────────────────────────────────
print()
passed = sum(1 for _, _, s in results if s == PASS)
failed = sum(1 for _, _, s in results if s == FAIL)
skipped = sum(1 for _, _, s in results if s == SKIP)
total = len(results)
print(f"Results: {passed}/{total} passed, {failed} failed, {skipped} skipped")
if failed > 0:
    sys.exit(1)
