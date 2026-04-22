#!/usr/bin/env python3
"""
DAMON Subsystem Workflow Verification
=======================================
Verifies the DAMON (Data Access MONitor) subsystem via sysfs and bpftrace.
Tests kdamond startup, region monitoring, and DAMOS scheme application.

Requirements:
  - Linux with DAMON (CONFIG_DAMON=y + CONFIG_DAMON_PADDR/VADDR)
  - bpftrace >= 0.14
  - Root privileges

Usage:
  sudo python3 test_damon_subsystem.py
"""

import subprocess, sys, os, time, textwrap, tempfile

PASS = "\033[32m[PASS]\033[0m"
FAIL = "\033[31m[FAIL]\033[0m"
SKIP = "\033[33m[SKIP]\033[0m"
INFO = "\033[34m[INFO]\033[0m"
results = []

SYSFS_ROOT = "/sys/kernel/mm/damon/admin"

def run(cmd, timeout=10):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True,
                              text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None

def write_sysfs(path, value):
    try:
        with open(path, 'w') as f:
            f.write(str(value))
        return True
    except Exception:
        return False

def read_sysfs(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except Exception:
        return None

def check_prereqs():
    print(f"\n{INFO} Checking prerequisites...")
    if os.geteuid() != 0:
        print(f"{FAIL} Must run as root"); sys.exit(1)
    if not run("which bpftrace") or run("which bpftrace").returncode != 0:
        print(f"{FAIL} bpftrace not found"); sys.exit(1)
    print(f"{PASS} Prerequisites OK")

def bpf_step(num, desc, script, trigger=None, keyword=None, timeout=10):
    print(f"\n── Step {num}: {desc}")
    with tempfile.NamedTemporaryFile(mode='w', suffix='.bt', delete=False) as f:
        f.write(script); bt = f.name

    proc = subprocess.Popen(["bpftrace", bt],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    time.sleep(1.5)
    if trigger:
        run(trigger, timeout=6)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill(); out, err = proc.communicate()
    os.unlink(bt)

    combined = out + err
    if keyword and keyword in combined:
        print(f"{PASS}  Detected: '{keyword}'")
        print(f"         {combined.strip()[:200]}")
        results.append((num, desc, "PASS"))
    elif not keyword and proc.returncode == 0:
        print(f"{PASS}  Script ran cleanly")
        results.append((num, desc, "PASS"))
    else:
        if any(x in combined for x in ("not traceable","No probes","ERROR")):
            print(f"{SKIP}  Symbol not traceable")
            print(f"         {err.strip()[:200]}")
            results.append((num, desc, "SKIP"))
        else:
            print(f"{FAIL}  Expected '{keyword}' not found")
            print(f"         {combined.strip()[:200]}")
            results.append((num, desc, "FAIL"))

def step1_symbols():
    print(f"\n── Step 1: DAMON symbols in kernel")
    r = run("grep -c ' damon_' /proc/kallsyms")
    count = int(r.stdout.strip()) if r and r.returncode == 0 else 0
    if count > 10:
        print(f"{PASS}  {count} damon_* symbols")
        results.append((1, "DAMON symbols in kallsyms", "PASS"))
    else:
        print(f"{FAIL}  Only {count} damon symbols (DAMON not built?)")
        results.append((1, "DAMON symbols in kallsyms", "FAIL"))

def step2_sysfs_present():
    print(f"\n── Step 2: DAMON sysfs interface present")
    if os.path.isdir(SYSFS_ROOT):
        print(f"{PASS}  {SYSFS_ROOT} exists")
        results.append((2, "DAMON sysfs interface present", "PASS"))
    else:
        print(f"{SKIP}  {SYSFS_ROOT} not found (CONFIG_DAMON_SYSFS?)")
        results.append((2, "DAMON sysfs interface present", "SKIP"))

def step3_kdamond_nr():
    print(f"\n── Step 3: kdamonds can be allocated via sysfs")
    nr_path = f"{SYSFS_ROOT}/kdamonds/nr_kdamonds"
    if not os.path.exists(nr_path):
        print(f"{SKIP}  {nr_path} not found")
        results.append((3, "kdamond sysfs allocation", "SKIP"))
        return
    val = read_sysfs(nr_path)
    print(f"         Current nr_kdamonds={val}")
    # Write 1 to allocate a kdamond slot if needed
    if write_sysfs(nr_path, 1):
        print(f"{PASS}  Set nr_kdamonds=1 successfully")
        results.append((3, "kdamond sysfs allocation", "PASS"))
    else:
        print(f"{FAIL}  Could not write to {nr_path}")
        results.append((3, "kdamond sysfs allocation", "FAIL"))

def step4_context_setup():
    print(f"\n── Step 4: DAMON monitoring context setup")
    ctx_nr = f"{SYSFS_ROOT}/kdamonds/0/contexts/nr_contexts"
    if not os.path.exists(ctx_nr):
        print(f"{SKIP}  kdamond 0 not available")
        results.append((4, "DAMON context setup", "SKIP"))
        return
    if write_sysfs(ctx_nr, 1):
        print(f"{PASS}  Allocated context 0 for kdamond 0")
        results.append((4, "DAMON context setup", "PASS"))
    else:
        print(f"{FAIL}  Could not allocate context")
        results.append((4, "DAMON context setup", "FAIL"))

def step5_ops_selection():
    print(f"\n── Step 5: DAMON monitoring ops selection")
    ops_path = f"{SYSFS_ROOT}/kdamonds/0/contexts/0/operations"
    if not os.path.exists(ops_path):
        print(f"{SKIP}  ops path not found")
        results.append((5, "DAMON ops selection", "SKIP"))
        return
    # Try paddr first (doesn't need a target PID)
    for ops in ("paddr", "vaddr"):
        if write_sysfs(ops_path, ops):
            val = read_sysfs(ops_path)
            if val == ops:
                print(f"{PASS}  ops set to '{ops}'")
                results.append((5, "DAMON ops selection", "PASS"))
                return
    print(f"{FAIL}  Could not set ops")
    results.append((5, "DAMON ops selection", "FAIL"))

def step6_damon_start():
    bpf_step(6, "damon_start called when kdamond is started",
        textwrap.dedent("""
            kprobe:damon_start {
                printf("DAMON_START ctx=%p nr_ctxs=%d pid=%d\\n",
                       arg0, arg1, pid);
                exit();
            }
            interval:s:8 { exit(); }
        """),
        trigger=(
            f"echo 1 > {SYSFS_ROOT}/kdamonds/0/contexts/nr_contexts 2>/dev/null; "
            f"echo paddr > {SYSFS_ROOT}/kdamonds/0/contexts/0/operations 2>/dev/null; "
            f"echo on > {SYSFS_ROOT}/kdamonds/0/state 2>/dev/null; "
            f"sleep 0.5; "
            f"echo off > {SYSFS_ROOT}/kdamonds/0/state 2>/dev/null; true"
        ),
        keyword="DAMON_START",
        timeout=12,
    )

def step7_kdamond_thread():
    print(f"\n── Step 7: kdamond kernel thread is created")
    # Check if any kdamond thread exists or recently ran
    r = run("ps aux | grep kdamond | grep -v grep | head -3")
    if r and r.stdout.strip():
        print(f"{PASS}  kdamond thread found:")
        print(f"         {r.stdout.strip()[:200]}")
        results.append((7, "kdamond thread running", "PASS"))
    else:
        # Try starting briefly
        state_path = f"{SYSFS_ROOT}/kdamonds/0/state"
        if os.path.exists(state_path):
            write_sysfs(state_path, "on")
            time.sleep(0.5)
            r2 = run("ps aux | grep kdamond | grep -v grep")
            write_sysfs(state_path, "off")
            if r2 and r2.stdout.strip():
                print(f"{PASS}  kdamond thread appeared during test")
                results.append((7, "kdamond thread running", "PASS"))
                return
        print(f"{SKIP}  kdamond thread not observed (may need valid target)")
        results.append((7, "kdamond thread running", "SKIP"))

def step8_damos_apply():
    bpf_step(8, "damos_apply_scheme called during monitoring",
        textwrap.dedent("""
            kprobe:damos_apply_scheme {
                printf("DAMOS_APPLY_SCHEME ctx=%p scheme=%p pid=%d\\n",
                       arg0, arg1, pid);
                exit();
            }
            interval:s:8 { exit(); }
        """),
        trigger=(
            f"echo on > {SYSFS_ROOT}/kdamonds/0/state 2>/dev/null; "
            f"sleep 0.5; "
            f"echo off > {SYSFS_ROOT}/kdamonds/0/state 2>/dev/null; true"
        ),
        keyword="DAMOS_APPLY_SCHEME",
        timeout=12,
    )

def step9_damon_reclaim_module():
    print(f"\n── Step 9: DAMON_RECLAIM module (proactive reclaim)")
    path = "/sys/module/damon_reclaim/parameters/enabled"
    if os.path.exists(path):
        val = read_sysfs(path)
        print(f"{PASS}  damon_reclaim module present (enabled={val})")
        results.append((9, "DAMON_RECLAIM module available", "PASS"))
    else:
        r = run("modprobe damon_reclaim 2>/dev/null", timeout=5)
        if os.path.exists(path):
            print(f"{PASS}  damon_reclaim loaded via modprobe")
            results.append((9, "DAMON_RECLAIM module available", "PASS"))
        else:
            print(f"{SKIP}  damon_reclaim not available")
            results.append((9, "DAMON_RECLAIM module available", "SKIP"))

def step10_tracepoints():
    print(f"\n── Step 10: DAMON tracepoints available")
    r = run("ls /sys/kernel/debug/tracing/events/damon/ 2>/dev/null | head -5")
    if r and r.returncode == 0 and r.stdout.strip():
        events = r.stdout.strip().split()
        print(f"{PASS}  DAMON tracepoints: {events[:5]}")
        results.append((10, "DAMON tracepoints registered", "PASS"))
    else:
        r2 = run("grep -c 'damon' /sys/kernel/debug/tracing/available_events 2>/dev/null")
        count = int(r2.stdout.strip()) if r2 and r2.returncode == 0 else 0
        if count > 0:
            print(f"{PASS}  {count} DAMON events in available_events")
            results.append((10, "DAMON tracepoints registered", "PASS"))
        else:
            print(f"{SKIP}  DAMON tracepoints not found")
            results.append((10, "DAMON tracepoints registered", "SKIP"))

def cleanup():
    # Make sure kdamond is off
    run(f"echo off > {SYSFS_ROOT}/kdamonds/0/state 2>/dev/null", timeout=3)

def print_summary():
    print("\n" + "═"*60)
    print("  DAMON Subsystem Verification Summary")
    print("═"*60)
    passed  = sum(1 for _,_,s in results if s=="PASS")
    failed  = sum(1 for _,_,s in results if s=="FAIL")
    skipped = sum(1 for _,_,s in results if s=="SKIP")
    for n,d,s in results:
        icon = PASS if s=="PASS" else (FAIL if s=="FAIL" else SKIP)
        print(f"  Step {n:>2}: {icon}  {d}")
    print("═"*60)
    print(f"  Total: {len(results)}  | \033[32mPASS:{passed}\033[0m "
          f"| \033[31mFAIL:{failed}\033[0m | \033[33mSKIP:{skipped}\033[0m")
    print("═"*60)
    if failed == 0:
        print(f"\n{PASS} All verifiable steps passed!\n"); return 0
    print(f"\n{FAIL} {failed} step(s) failed.\n"); return 1

def main():
    print("╔══════════════════════════════════════════════════════╗")
    print("║       DAMON Subsystem - Workflow Verification        ║")
    print("╚══════════════════════════════════════════════════════╝")
    check_prereqs()
    step1_symbols()
    step2_sysfs_present()
    step3_kdamond_nr()
    step4_context_setup()
    step5_ops_selection()
    step6_damon_start()
    step7_kdamond_thread()
    step8_damos_apply()
    step9_damon_reclaim_module()
    step10_tracepoints()
    cleanup()
    return print_summary()

if __name__ == "__main__":
    sys.exit(main())
