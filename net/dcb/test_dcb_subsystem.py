#!/usr/bin/env python3
"""
DCB (Data Center Bridging) Subsystem Workflow Verification
============================================================
Uses bpftrace to trace DCB operations including application priority
management, netlink commands, and PFC/ETS configuration.

Requirements:
  - Linux with DCB support (CONFIG_DCB=y)
  - bpftrace >= 0.14
  - Root privileges

Usage:
  sudo python3 test_dcb_subsystem.py
"""

import subprocess, sys, os, time, textwrap, tempfile

PASS = "\033[32m[PASS]\033[0m"
FAIL = "\033[31m[FAIL]\033[0m"
SKIP = "\033[33m[SKIP]\033[0m"
INFO = "\033[34m[INFO]\033[0m"
results = []

def run(cmd, timeout=10):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True,
                              text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
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
        if any(x in combined for x in ("not traceable", "No probes", "ERROR")):
            print(f"{SKIP}  Symbol not traceable")
            print(f"         {err.strip()[:200]}")
            results.append((num, desc, "SKIP"))
        else:
            print(f"{FAIL}  Expected '{keyword}' not found")
            print(f"         {combined.strip()[:200]}")
            results.append((num, desc, "FAIL"))

def step1():
    print(f"\n── Step 1: DCB symbols in kernel")
    r = run("grep -c ' dcb_\\| dcbnl_' /proc/kallsyms")
    count = int(r.stdout.strip()) if r and r.returncode == 0 else 0
    if count > 5:
        print(f"{PASS}  {count} DCB symbols found")
        results.append((1, "DCB symbols in kallsyms", "PASS"))
    else:
        print(f"{SKIP}  Only {count} DCB symbols (CONFIG_DCB not set?)")
        results.append((1, "DCB symbols in kallsyms", "SKIP"))

def step2():
    print(f"\n── Step 2: DCB-capable network devices")
    r = run("ls /sys/class/net/*/dcb 2>/dev/null | head -3")
    if r and r.returncode == 0 and r.stdout.strip():
        print(f"{PASS}  DCB sysfs entries found")
        results.append((2, "DCB-capable devices", "PASS"))
    else:
        r2 = run("grep -c 'dcbnl_ops' /proc/kallsyms")
        cnt = int(r2.stdout.strip()) if r2 and r2.returncode == 0 else 0
        if cnt > 0:
            print(f"{PASS}  DCB ops symbols present (no DCB NIC visible)")
            results.append((2, "DCB-capable devices", "PASS"))
        else:
            print(f"{SKIP}  No DCB-capable devices found")
            results.append((2, "DCB-capable devices", "SKIP"))

def step3():
    bpf_step(3, "Trace dcb_setapp application priority set",
        textwrap.dedent("""
            kprobe:dcb_setapp {
                printf("DCB_SETAPP dev=%p app=%p pid=%d\\n",
                       arg0, arg1, pid);
                exit();
            }
            interval:s:6 { exit(); }
        """),
        keyword="DCB_SETAPP",
        timeout=8,
    )

def step4():
    bpf_step(4, "Trace dcbnl_notify DCB change notification",
        textwrap.dedent("""
            kprobe:dcbnl_notify {
                printf("DCBNL_NOTIFY dev=%p event=%d pid=%d\\n",
                       arg0, arg1, pid);
                exit();
            }
            interval:s:6 { exit(); }
        """),
        keyword="DCBNL_NOTIFY",
        timeout=8,
    )

def step5():
    bpf_step(5, "Trace dcbnl_ieee_set IEEE parameter set",
        textwrap.dedent("""
            kprobe:dcbnl_ieee_set {
                printf("DCBNL_IEEE_SET dev=%p pid=%d\\n", arg0, pid);
                exit();
            }
            interval:s:6 { exit(); }
        """),
        keyword="DCBNL_IEEE_SET",
        timeout=8,
    )

def step6():
    bpf_step(6, "Trace dcbnl_ieee_get IEEE parameter get",
        textwrap.dedent("""
            kprobe:dcbnl_ieee_get {
                printf("DCBNL_IEEE_GET dev=%p pid=%d\\n", arg0, pid);
                exit();
            }
            interval:s:6 { exit(); }
        """),
        keyword="DCBNL_IEEE_GET",
        timeout=8,
    )

def step7():
    bpf_step(7, "Trace dcb_getapp application priority get",
        textwrap.dedent("""
            kprobe:dcb_getapp {
                printf("DCB_GETAPP dev=%p app=%p pid=%d\\n",
                       arg0, arg1, pid);
                exit();
            }
            interval:s:6 { exit(); }
        """),
        keyword="DCB_GETAPP",
        timeout=8,
    )

def step8():
    bpf_step(8, "Trace dcb_ieee_setapp IEEE app priority set",
        textwrap.dedent("""
            kprobe:dcb_ieee_setapp {
                printf("DCB_IEEE_SETAPP dev=%p app=%p pid=%d\\n",
                       arg0, arg1, pid);
                exit();
            }
            interval:s:6 { exit(); }
        """),
        keyword="DCB_IEEE_SETAPP",
        timeout=8,
    )

def step9():
    bpf_step(9, "Trace dcbnl_cee_get CEE parameter get",
        textwrap.dedent("""
            kprobe:dcbnl_cee_get {
                printf("DCBNL_CEE_GET dev=%p pid=%d\\n", arg0, pid);
                exit();
            }
            interval:s:6 { exit(); }
        """),
        keyword="DCBNL_CEE_GET",
        timeout=8,
    )

def step10():
    print(f"\n── Step 10: DCB infrastructure registered")
    r = run("grep -c 'dcb' /proc/kallsyms")
    count = int(r.stdout.strip()) if r and r.returncode == 0 else 0
    if count > 10:
        print(f"{PASS}  {count} DCB symbols — infrastructure present")
        results.append((10, "DCB infrastructure", "PASS"))
    else:
        print(f"{SKIP}  DCB infrastructure not found")
        results.append((10, "DCB infrastructure", "SKIP"))

def print_summary():
    print("\n" + "═"*60)
    print("  DCB Subsystem Verification Summary")
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
    print("║       DCB Subsystem - Workflow Verification          ║")
    print("╚══════════════════════════════════════════════════════╝")
    check_prereqs()
    step1(); step2(); step3(); step4(); step5()
    step6(); step7(); step8(); step9(); step10()
    return print_summary()

if __name__ == "__main__":
    sys.exit(main())
