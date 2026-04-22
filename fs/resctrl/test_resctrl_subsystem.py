#!/usr/bin/env python3
"""
test_resctrl_subsystem.py — bpftrace verification of resctrl (Intel RDT).

Steps
-----
1.  Check resctrl filesystem mount support
2.  Probe rdtgroup_mkdir           — create a new resctrl group
3.  Probe rdtgroup_rmdir           — remove a resctrl group
4.  Probe rdtgroup_tasks_write     — assign tasks to a group
5.  Probe resctrl_arch_update_domains — MSR programming for domain update
6.  Probe mondata_show             — read CMT/MBM counter
7.  Mount /sys/fs/resctrl and verify info/ structure
8.  Create + delete a test CTRL_MON group
9.  Probe rmid_alloc               — RMID allocation
10. Check /sys/fs/resctrl/info/L3/cbm_mask
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
RESCTRL_MOUNT = "/sys/fs/resctrl"


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


print("\n=== resctrl (Intel RDT) bpftrace verification ===\n")


def ensure_mounted() -> bool:
    """Return True if resctrl is mounted."""
    try:
        r = subprocess.run(["findmnt", "-t", "resctrl", RESCTRL_MOUNT],
                           capture_output=True, timeout=5)
        if r.returncode == 0:
            return True
        r2 = subprocess.run(["mount", "-t", "resctrl", "resctrl", RESCTRL_MOUNT],
                             capture_output=True, timeout=5)
        return r2.returncode == 0
    except Exception:
        return False


rdt_available = ensure_mounted()
if rdt_available:
    print(f"  [info] resctrl mounted at {RESCTRL_MOUNT}\n")
else:
    print("  [info] resctrl not available — most steps will SKIP\n")

TEST_GROUP = os.path.join(RESCTRL_MOUNT, "bpftrace_test_grp")


def create_group():
    if rdt_available:
        os.makedirs(TEST_GROUP, exist_ok=True)


def delete_group():
    if os.path.exists(TEST_GROUP):
        try:
            os.rmdir(TEST_GROUP)
        except Exception:
            pass


# ── Step 1: resctrl mount check ──────────────────────────────────────────────
print(f"  Step  1: {'resctrl filesystem available':52s}", end=" ")
if rdt_available:
    print(PASS)
    results.append((1, "resctrl available", PASS))
else:
    print(SKIP)
    results.append((1, "resctrl available", SKIP))

# ── Step 2: rdtgroup_mkdir ───────────────────────────────────────────────────
prog2 = """
kprobe:rdtgroup_mkdir {
    printf("HIT rdtgroup_mkdir\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(2, "rdtgroup_mkdir kprobe", prog2, trigger=create_group, timeout=10)

# ── Step 3: rdtgroup_rmdir ───────────────────────────────────────────────────
prog3 = """
kprobe:rdtgroup_rmdir {
    printf("HIT rdtgroup_rmdir\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(3, "rdtgroup_rmdir kprobe", prog3, trigger=delete_group, timeout=10)

# ── Step 4: rdtgroup_tasks_write ─────────────────────────────────────────────
prog4 = """
kprobe:rdtgroup_tasks_write {
    printf("HIT rdtgroup_tasks_write\\n");
    exit();
}
interval:s:5 { exit(); }
"""

def write_tasks():
    create_group()
    tasks_file = os.path.join(TEST_GROUP, "tasks")
    if os.path.exists(tasks_file):
        try:
            with open(tasks_file, "w") as f:
                f.write(str(os.getpid()))
        except Exception:
            pass

check(4, "rdtgroup_tasks_write kprobe", prog4, trigger=write_tasks, timeout=10)

# ── Step 5: resctrl_arch_update_domains ──────────────────────────────────────
prog5 = """
kprobe:resctrl_arch_update_domains {
    printf("HIT resctrl_arch_update_domains\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(5, "resctrl_arch_update_domains kprobe", prog5, timeout=8)

# ── Step 6: mondata_show / rdtgroup_mondata_show ──────────────────────────────
prog6 = """
kprobe:rdtgroup_mondata_show {
    printf("HIT rdtgroup_mondata_show\\n");
    exit();
}
interval:s:5 { exit(); }
"""

def read_mondata():
    if rdt_available:
        mon_dir = os.path.join(RESCTRL_MOUNT, "mon_data")
        if os.path.exists(mon_dir):
            for d in os.listdir(mon_dir)[:2]:
                p = os.path.join(mon_dir, d, "llc_occupancy")
                if os.path.exists(p):
                    try:
                        open(p).read()
                    except Exception:
                        pass

check(6, "rdtgroup_mondata_show kprobe", prog6, trigger=read_mondata, timeout=10)

# ── Step 7: resctrl info/ structure ──────────────────────────────────────────
print(f"  Step  7: {'/sys/fs/resctrl/info/ exists':52s}", end=" ")
info_dir = os.path.join(RESCTRL_MOUNT, "info")
if os.path.isdir(info_dir):
    print(PASS)
    results.append((7, "resctrl info dir", PASS))
else:
    print(SKIP)
    results.append((7, "resctrl info dir", SKIP))

# ── Step 8: create + delete test group ───────────────────────────────────────
print(f"  Step  8: {'create + delete test CTRL_MON group':52s}", end=" ")
if rdt_available:
    create_group()
    if os.path.exists(TEST_GROUP):
        delete_group()
        if not os.path.exists(TEST_GROUP):
            print(PASS)
            results.append((8, "group create/delete", PASS))
        else:
            print(FAIL)
            results.append((8, "group create/delete", FAIL))
    else:
        print(FAIL)
        results.append((8, "group create/delete", FAIL))
else:
    print(SKIP)
    results.append((8, "group create/delete", SKIP))

# ── Step 9: rmid_alloc ───────────────────────────────────────────────────────
prog9 = """
kprobe:rmid_alloc {
    printf("HIT rmid_alloc\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(9, "rmid_alloc kprobe", prog9, trigger=create_group, timeout=10)
delete_group()

# ── Step 10: L3 CBM mask ─────────────────────────────────────────────────────
print(f"  Step 10: {'/sys/fs/resctrl/info/L3/cbm_mask':52s}", end=" ")
cbm_paths = [
    os.path.join(RESCTRL_MOUNT, "info", "L3", "cbm_mask"),
    os.path.join(RESCTRL_MOUNT, "info", "L3_MON", "num_rmids"),
]
found = any(os.path.exists(p) for p in cbm_paths)
if found:
    val = open(cbm_paths[0]).read().strip() if os.path.exists(cbm_paths[0]) else "present"
    print(f"{PASS} ({val})")
    results.append((10, "L3 cbm_mask", PASS))
else:
    print(SKIP)
    results.append((10, "L3 cbm_mask", SKIP))

# ── Summary ──────────────────────────────────────────────────────────────────
print()
passed = sum(1 for _, _, s in results if s == PASS)
failed = sum(1 for _, _, s in results if s == FAIL)
skipped = sum(1 for _, _, s in results if s == SKIP)
total = len(results)
print(f"Results: {passed}/{total} passed, {failed} failed, {skipped} skipped")
if failed > 0:
    sys.exit(1)
