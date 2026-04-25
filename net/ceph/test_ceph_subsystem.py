#!/usr/bin/env python3
"""
Ceph Messenger Subsystem Workflow Verification
=================================================
Uses bpftrace to trace Ceph kernel messenger operations including
connection management, message sending, and client initialization.

Requirements:
  - Linux with Ceph support (CONFIG_CEPH_LIB=y/m)
  - bpftrace >= 0.14
  - Root privileges

Usage:
  sudo python3 test_ceph_subsystem.py
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
    print(f"\n── Step 1: Ceph messenger symbols in kernel")
    r = run("grep -c ' ceph_con_\\| ceph_msg_\\| ceph_messenger' /proc/kallsyms")
    count = int(r.stdout.strip()) if r and r.returncode == 0 else 0
    if count > 5:
        print(f"{PASS}  {count} ceph messenger symbols found")
        results.append((1, "Ceph messenger symbols in kallsyms", "PASS"))
    else:
        print(f"{SKIP}  Only {count} ceph symbols (not built or modular)")
        results.append((1, "Ceph messenger symbols in kallsyms", "SKIP"))

def step2():
    print(f"\n── Step 2: Ceph module loaded")
    r = run("lsmod | grep -i ceph")
    if r and r.returncode == 0 and r.stdout.strip():
        print(f"{PASS}  Ceph module(s) loaded: {r.stdout.strip()[:120]}")
        results.append((2, "Ceph module loaded", "PASS"))
    else:
        r2 = run("grep -c ' ceph_con_send' /proc/kallsyms")
        cnt = int(r2.stdout.strip()) if r2 and r2.returncode == 0 else 0
        if cnt > 0:
            print(f"{PASS}  Ceph built-in")
            results.append((2, "Ceph module loaded", "PASS"))
        else:
            print(f"{SKIP}  Ceph module not loaded")
            results.append((2, "Ceph module loaded", "SKIP"))

def step3():
    bpf_step(3, "Trace ceph_con_send message queuing",
        textwrap.dedent("""
            kprobe:ceph_con_send {
                printf("CEPH_CON_SEND con=%p msg=%p pid=%d\\n",
                       arg0, arg1, pid);
                exit();
            }
            interval:s:6 { exit(); }
        """),
        keyword="CEPH_CON_SEND",
        timeout=8,
    )

def step4():
    bpf_step(4, "Trace ceph_msg_data_add_pages data attachment",
        textwrap.dedent("""
            kprobe:ceph_msg_data_add_pages {
                printf("CEPH_MSG_DATA_ADD_PAGES msg=%p pages=%p\\n",
                       arg0, arg1);
                exit();
            }
            interval:s:6 { exit(); }
        """),
        keyword="CEPH_MSG_DATA_ADD_PAGES",
        timeout=8,
    )

def step5():
    bpf_step(5, "Trace ceph_con_open connection open",
        textwrap.dedent("""
            kprobe:ceph_con_open {
                printf("CEPH_CON_OPEN con=%p type=%d pid=%d\\n",
                       arg0, arg1, pid);
                exit();
            }
            interval:s:6 { exit(); }
        """),
        keyword="CEPH_CON_OPEN",
        timeout=8,
    )

def step6():
    bpf_step(6, "Trace ceph_msg_new message allocation",
        textwrap.dedent("""
            kprobe:ceph_msg_new {
                printf("CEPH_MSG_NEW type=%d front_len=%d\\n",
                       arg0, arg1);
                exit();
            }
            interval:s:6 { exit(); }
        """),
        keyword="CEPH_MSG_NEW",
        timeout=8,
    )

def step7():
    bpf_step(7, "Trace ceph_con_close connection close",
        textwrap.dedent("""
            kprobe:ceph_con_close {
                printf("CEPH_CON_CLOSE con=%p pid=%d\\n", arg0, pid);
                exit();
            }
            interval:s:6 { exit(); }
        """),
        keyword="CEPH_CON_CLOSE",
        timeout=8,
    )

def step8():
    bpf_step(8, "Trace ceph_monc_init monitor client init",
        textwrap.dedent("""
            kprobe:ceph_monc_init {
                printf("CEPH_MONC_INIT client=%p pid=%d\\n", arg0, pid);
                exit();
            }
            interval:s:6 { exit(); }
        """),
        keyword="CEPH_MONC_INIT",
        timeout=8,
    )

def step9():
    bpf_step(9, "Trace ceph_osdc_init OSD client init",
        textwrap.dedent("""
            kprobe:ceph_osdc_init {
                printf("CEPH_OSDC_INIT client=%p pid=%d\\n", arg0, pid);
                exit();
            }
            interval:s:6 { exit(); }
        """),
        keyword="CEPH_OSDC_INIT",
        timeout=8,
    )

def step10():
    print(f"\n── Step 10: Ceph debugfs / module presence")
    r = run("ls /sys/module/libceph/ 2>/dev/null || ls /sys/module/ceph/ 2>/dev/null")
    if r and r.returncode == 0 and r.stdout.strip():
        print(f"{PASS}  Ceph module sysfs present")
        results.append((10, "Ceph module sysfs presence", "PASS"))
    else:
        r2 = run("grep -c 'ceph_' /proc/kallsyms")
        cnt = int(r2.stdout.strip()) if r2 and r2.returncode == 0 else 0
        if cnt > 20:
            print(f"{PASS}  {cnt} ceph symbols in kallsyms")
            results.append((10, "Ceph module sysfs presence", "PASS"))
        else:
            print(f"{SKIP}  Ceph module not present")
            results.append((10, "Ceph module sysfs presence", "SKIP"))

def print_summary():
    print("\n" + "═"*60)
    print("  Ceph Messenger Subsystem Verification Summary")
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
    print("║   Ceph Messenger Subsystem - Workflow Verification   ║")
    print("╚══════════════════════════════════════════════════════╝")
    check_prereqs()
    step1(); step2(); step3(); step4(); step5()
    step6(); step7(); step8(); step9(); step10()
    return print_summary()

if __name__ == "__main__":
    sys.exit(main())
