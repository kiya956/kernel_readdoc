#!/usr/bin/env python3
"""
test_kmsan_subsystem.py — bpftrace-based verification of KMSAN (Kernel Memory SANitizer).

Because KMSAN only exists in CONFIG_KMSAN=y kernels (special fuzzing kernels),
most probes will SKIP on a normal production kernel.  The test also checks
for config/sysfs presence and the existence of KMSAN symbols in kallsyms.

Steps
-----
1.  Check CONFIG_KMSAN in kernel config
2.  Probe kmsan_report                      — uninitialized-use report
3.  Probe kmsan_alloc_page                  — new page shadow setup
4.  Probe kmsan_slab_alloc / kmsan_kmalloc  — slab shadow setup
5.  Probe kmsan_copy_to_user               — safety check before user copy
6.  Probe kmsan_internal_poison_memory     — explicit poison
7.  Probe kmsan_internal_unpoison_memory   — explicit unpoison
8.  Probe kmsan_task_create                — per-task KMSAN state init
9.  Check /sys/kernel/debug/kmsan          — debugfs presence
10. Verify KMSAN symbols present in /proc/kallsyms
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


print("\n=== KMSAN (Kernel Memory SANitizer) bpftrace verification ===\n")
print("  NOTE: Most steps will SKIP on non-KMSAN kernels (expected).\n")


# ── Step 1: CONFIG_KMSAN in kernel config ────────────────────────────────────
print(f"  Step  1: {'CONFIG_KMSAN in kernel config':52s}", end=" ")
kmsan_enabled = False
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
        if "CONFIG_KMSAN=y" in data:
            kmsan_enabled = True
            break
    except Exception:
        pass
if kmsan_enabled:
    print(PASS)
    results.append((1, "CONFIG_KMSAN", PASS))
else:
    print(SKIP)
    results.append((1, "CONFIG_KMSAN", SKIP))

# ── Step 2: kmsan_report ─────────────────────────────────────────────────────
prog2 = """
kprobe:kmsan_report {
    printf("HIT kmsan_report\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(2, "kmsan_report kprobe", prog2, timeout=8)

# ── Step 3: kmsan_alloc_page ─────────────────────────────────────────────────
prog3 = """
kprobe:kmsan_alloc_page {
    printf("HIT kmsan_alloc_page\\n");
    exit();
}
interval:s:5 { exit(); }
"""

def alloc_trigger():
    # Allocate a small object to trigger the page allocator
    subprocess.run(["dd", "if=/dev/zero", "of=/dev/null", "bs=4096", "count=1"],
                   capture_output=True, timeout=5)

check(3, "kmsan_alloc_page kprobe", prog3, trigger=alloc_trigger, timeout=10)

# ── Step 4: kmsan_slab_alloc / kmsan_kmalloc ─────────────────────────────────
prog4 = """
kprobe:kmsan_slab_alloc,
kprobe:kmsan_kmalloc {
    printf("HIT kmsan_slab/kmalloc\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(4, "kmsan_slab_alloc / kmsan_kmalloc kprobe", prog4,
      trigger=alloc_trigger, timeout=10)

# ── Step 5: kmsan_copy_to_user ───────────────────────────────────────────────
prog5 = """
kprobe:kmsan_copy_to_user {
    printf("HIT kmsan_copy_to_user\\n");
    exit();
}
interval:s:5 { exit(); }
"""

def copy_trigger():
    # Reading /proc/version triggers copy_to_user
    try:
        open("/proc/version").read()
    except Exception:
        pass

check(5, "kmsan_copy_to_user kprobe", prog5, trigger=copy_trigger, timeout=10)

# ── Step 6: kmsan_internal_poison_memory ────────────────────────────────────
prog6 = """
kprobe:kmsan_internal_poison_memory {
    printf("HIT kmsan_internal_poison_memory\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(6, "kmsan_internal_poison_memory kprobe", prog6,
      trigger=alloc_trigger, timeout=10)

# ── Step 7: kmsan_internal_unpoison_memory ───────────────────────────────────
prog7 = """
kprobe:kmsan_internal_unpoison_memory {
    printf("HIT kmsan_internal_unpoison_memory\\n");
    exit();
}
interval:s:5 { exit(); }
"""

def zero_alloc_trigger():
    # __GFP_ZERO triggers unpoison path
    subprocess.run(["dd", "if=/dev/zero", "of=/dev/null", "bs=4096", "count=1"],
                   capture_output=True, timeout=5)
    open("/proc/loadavg").read()

check(7, "kmsan_internal_unpoison_memory kprobe", prog7,
      trigger=zero_alloc_trigger, timeout=10)

# ── Step 8: kmsan_task_create ────────────────────────────────────────────────
prog8 = """
kprobe:kmsan_task_create {
    printf("HIT kmsan_task_create\\n");
    exit();
}
interval:s:8 { exit(); }
"""

def fork_trigger():
    subprocess.run(["true"], capture_output=True, timeout=5)

check(8, "kmsan_task_create kprobe", prog8, trigger=fork_trigger, timeout=12)

# ── Step 9: debugfs /sys/kernel/debug/kmsan ──────────────────────────────────
print(f"  Step  9: {'/sys/kernel/debug/kmsan exists':52s}", end=" ")
dbg = "/sys/kernel/debug/kmsan"
if os.path.exists(dbg):
    print(PASS)
    results.append((9, "debugfs kmsan", PASS))
else:
    print(SKIP)
    results.append((9, "debugfs kmsan", SKIP))

# ── Step 10: KMSAN symbols in kallsyms ───────────────────────────────────────
print(f"  Step 10: {'KMSAN symbols in /proc/kallsyms':52s}", end=" ")
kmsan_symbols = False
try:
    with open("/proc/kallsyms") as f:
        for line in f:
            if "kmsan_report" in line or "kmsan_alloc_page" in line:
                kmsan_symbols = True
                break
except Exception:
    pass
if kmsan_symbols:
    print(PASS)
    results.append((10, "KMSAN kallsyms", PASS))
else:
    print(SKIP)
    results.append((10, "KMSAN kallsyms", SKIP))

# ── Summary ──────────────────────────────────────────────────────────────────
print()
passed = sum(1 for _, _, s in results if s == PASS)
failed = sum(1 for _, _, s in results if s == FAIL)
skipped = sum(1 for _, _, s in results if s == SKIP)
total = len(results)
print(f"Results: {passed}/{total} passed, {failed} failed, {skipped} skipped")
if failed > 0:
    sys.exit(1)
