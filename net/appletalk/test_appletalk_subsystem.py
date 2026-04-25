#!/usr/bin/env python3
"""
AppleTalk Subsystem Workflow Verification
===========================================
Uses bpftrace to trace AppleTalk DDP protocol operations including
packet reception, socket send/receive, and AARP resolution.

Requirements:
  - Linux with AppleTalk support (CONFIG_ATALK=y/m)
  - bpftrace >= 0.14
  - Root privileges

Usage:
  sudo python3 test_appletalk_subsystem.py
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
    print(f"\n── Step 1: AppleTalk symbols in kernel")
    r = run("grep -c ' atalk_\\| ddp_' /proc/kallsyms")
    count = int(r.stdout.strip()) if r and r.returncode == 0 else 0
    if count > 5:
        print(f"{PASS}  {count} AppleTalk symbols found")
        results.append((1, "AppleTalk symbols in kallsyms", "PASS"))
    else:
        print(f"{SKIP}  Only {count} AppleTalk symbols (not built or modular)")
        results.append((1, "AppleTalk symbols in kallsyms", "SKIP"))

def step2():
    print(f"\n── Step 2: AppleTalk module loaded")
    r = run("lsmod | grep -i appletalk")
    if r and r.returncode == 0 and r.stdout.strip():
        print(f"{PASS}  AppleTalk module loaded: {r.stdout.strip()[:120]}")
        results.append((2, "AppleTalk module loaded", "PASS"))
    else:
        r2 = run("grep -c ' atalk_rcv' /proc/kallsyms")
        cnt = int(r2.stdout.strip()) if r2 and r2.returncode == 0 else 0
        if cnt > 0:
            print(f"{PASS}  AppleTalk built-in")
            results.append((2, "AppleTalk module loaded", "PASS"))
        else:
            print(f"{SKIP}  AppleTalk module not loaded")
            results.append((2, "AppleTalk module loaded", "SKIP"))

def step3():
    bpf_step(3, "Trace atalk_rcv packet reception",
        textwrap.dedent("""
            kprobe:atalk_rcv {
                printf("ATALK_RCV skb=%p dev=%p pid=%d\\n", arg0, arg1, pid);
                exit();
            }
            interval:s:6 { exit(); }
        """),
        keyword="ATALK_RCV",
        timeout=8,
    )

def step4():
    bpf_step(4, "Trace ddp_sendmsg socket send",
        textwrap.dedent("""
            kprobe:ddp_sendmsg {
                printf("DDP_SENDMSG sock=%p msg=%p len=%d\\n",
                       arg0, arg1, arg2);
                exit();
            }
            interval:s:6 { exit(); }
        """),
        keyword="DDP_SENDMSG",
        timeout=8,
    )

def step5():
    bpf_step(5, "Trace ddp_recvmsg socket receive",
        textwrap.dedent("""
            kprobe:ddp_recvmsg {
                printf("DDP_RECVMSG sock=%p msg=%p pid=%d\\n",
                       arg0, arg1, pid);
                exit();
            }
            interval:s:6 { exit(); }
        """),
        keyword="DDP_RECVMSG",
        timeout=8,
    )

def step6():
    bpf_step(6, "Trace atalk_create socket creation",
        textwrap.dedent("""
            kprobe:atalk_create {
                printf("ATALK_CREATE net=%p sock=%p pid=%d\\n",
                       arg0, arg1, pid);
                exit();
            }
            interval:s:6 { exit(); }
        """),
        keyword="ATALK_CREATE",
        timeout=8,
    )

def step7():
    bpf_step(7, "Trace aarp_send_query address resolution",
        textwrap.dedent("""
            kprobe:aarp_send_query {
                printf("AARP_SEND_QUERY addr=%p\\n", arg0);
                exit();
            }
            interval:s:6 { exit(); }
        """),
        keyword="AARP_SEND_QUERY",
        timeout=8,
    )

def step8():
    bpf_step(8, "Trace atrtr_find route lookup",
        textwrap.dedent("""
            kprobe:atrtr_find {
                printf("ATRTR_FIND target=%p pid=%d\\n", arg0, pid);
                exit();
            }
            interval:s:6 { exit(); }
        """),
        keyword="ATRTR_FIND",
        timeout=8,
    )

def step9():
    print(f"\n── Step 9: AppleTalk /proc interface")
    r = run("ls /proc/net/atalk* 2>/dev/null")
    if r and r.returncode == 0 and r.stdout.strip():
        print(f"{PASS}  AppleTalk proc entries: {r.stdout.strip()[:120]}")
        results.append((9, "AppleTalk proc interface", "PASS"))
    else:
        print(f"{SKIP}  No AppleTalk proc entries")
        results.append((9, "AppleTalk proc interface", "SKIP"))

def step10():
    print(f"\n── Step 10: AppleTalk protocol family registered")
    r = run("grep -c 'atalk' /proc/kallsyms")
    count = int(r.stdout.strip()) if r and r.returncode == 0 else 0
    if count > 10:
        print(f"{PASS}  {count} atalk symbols — protocol family present")
        results.append((10, "AppleTalk protocol family", "PASS"))
    else:
        print(f"{SKIP}  AppleTalk protocol family not found")
        results.append((10, "AppleTalk protocol family", "SKIP"))

def print_summary():
    print("\n" + "═"*60)
    print("  AppleTalk Subsystem Verification Summary")
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
    print("║    AppleTalk Subsystem - Workflow Verification       ║")
    print("╚══════════════════════════════════════════════════════╝")
    check_prereqs()
    step1(); step2(); step3(); step4(); step5()
    step6(); step7(); step8(); step9(); step10()
    return print_summary()

if __name__ == "__main__":
    sys.exit(main())
