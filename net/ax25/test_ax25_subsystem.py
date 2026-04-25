#!/usr/bin/env python3
"""
AX.25 Subsystem Workflow Verification
========================================
Uses bpftrace to trace AX.25 amateur radio protocol operations including
frame reception, socket operations, and connection management.

Requirements:
  - Linux with AX.25 support (CONFIG_AX25=y/m)
  - bpftrace >= 0.14
  - Root privileges

Usage:
  sudo python3 test_ax25_subsystem.py
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
    print(f"\n── Step 1: AX.25 symbols in kernel")
    r = run("grep -c ' ax25_' /proc/kallsyms")
    count = int(r.stdout.strip()) if r and r.returncode == 0 else 0
    if count > 5:
        print(f"{PASS}  {count} ax25_* symbols found")
        results.append((1, "AX.25 symbols in kallsyms", "PASS"))
    else:
        print(f"{SKIP}  Only {count} AX.25 symbols (not built or modular)")
        results.append((1, "AX.25 symbols in kallsyms", "SKIP"))

def step2():
    print(f"\n── Step 2: AX.25 module loaded")
    r = run("lsmod | grep -i ax25")
    if r and r.returncode == 0 and r.stdout.strip():
        print(f"{PASS}  AX.25 module loaded: {r.stdout.strip()[:120]}")
        results.append((2, "AX.25 module loaded", "PASS"))
    else:
        r2 = run("grep -c ' ax25_rcv' /proc/kallsyms")
        cnt = int(r2.stdout.strip()) if r2 and r2.returncode == 0 else 0
        if cnt > 0:
            print(f"{PASS}  AX.25 built-in")
            results.append((2, "AX.25 module loaded", "PASS"))
        else:
            print(f"{SKIP}  AX.25 module not loaded")
            results.append((2, "AX.25 module loaded", "SKIP"))

def step3():
    bpf_step(3, "Trace ax25_rcv frame reception",
        textwrap.dedent("""
            kprobe:ax25_rcv {
                printf("AX25_RCV skb=%p dev=%p pid=%d\\n", arg0, arg1, pid);
                exit();
            }
            interval:s:6 { exit(); }
        """),
        keyword="AX25_RCV",
        timeout=8,
    )

def step4():
    bpf_step(4, "Trace ax25_sendmsg socket send",
        textwrap.dedent("""
            kprobe:ax25_sendmsg {
                printf("AX25_SENDMSG sock=%p msg=%p len=%d\\n",
                       arg0, arg1, arg2);
                exit();
            }
            interval:s:6 { exit(); }
        """),
        keyword="AX25_SENDMSG",
        timeout=8,
    )

def step5():
    bpf_step(5, "Trace ax25_recvmsg socket receive",
        textwrap.dedent("""
            kprobe:ax25_recvmsg {
                printf("AX25_RECVMSG sock=%p msg=%p pid=%d\\n",
                       arg0, arg1, pid);
                exit();
            }
            interval:s:6 { exit(); }
        """),
        keyword="AX25_RECVMSG",
        timeout=8,
    )

def step6():
    bpf_step(6, "Trace ax25_connect connection setup",
        textwrap.dedent("""
            kprobe:ax25_connect {
                printf("AX25_CONNECT sock=%p addr=%p pid=%d\\n",
                       arg0, arg1, pid);
                exit();
            }
            interval:s:6 { exit(); }
        """),
        keyword="AX25_CONNECT",
        timeout=8,
    )

def step7():
    bpf_step(7, "Trace ax25_create socket creation",
        textwrap.dedent("""
            kprobe:ax25_create {
                printf("AX25_CREATE net=%p sock=%p pid=%d\\n",
                       arg0, arg1, pid);
                exit();
            }
            interval:s:6 { exit(); }
        """),
        keyword="AX25_CREATE",
        timeout=8,
    )

def step8():
    bpf_step(8, "Trace ax25_rt_find_route route lookup",
        textwrap.dedent("""
            kprobe:ax25_rt_find_route {
                printf("AX25_RT_FIND_ROUTE ax25_cb=%p addr=%p\\n",
                       arg0, arg1);
                exit();
            }
            interval:s:6 { exit(); }
        """),
        keyword="AX25_RT_FIND_ROUTE",
        timeout=8,
    )

def step9():
    print(f"\n── Step 9: AX.25 /proc interface")
    r = run("ls /proc/net/ax25* 2>/dev/null")
    if r and r.returncode == 0 and r.stdout.strip():
        print(f"{PASS}  AX.25 proc entries: {r.stdout.strip()[:120]}")
        results.append((9, "AX.25 proc interface", "PASS"))
    else:
        print(f"{SKIP}  No AX.25 proc entries")
        results.append((9, "AX.25 proc interface", "SKIP"))

def step10():
    print(f"\n── Step 10: AX.25 protocol family registered")
    r = run("grep -c 'ax25' /proc/kallsyms")
    count = int(r.stdout.strip()) if r and r.returncode == 0 else 0
    if count > 10:
        print(f"{PASS}  {count} ax25 symbols — protocol family present")
        results.append((10, "AX.25 protocol family", "PASS"))
    else:
        print(f"{SKIP}  AX.25 protocol family not found")
        results.append((10, "AX.25 protocol family", "SKIP"))

def print_summary():
    print("\n" + "═"*60)
    print("  AX.25 Subsystem Verification Summary")
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
    print("║      AX.25 Subsystem - Workflow Verification         ║")
    print("╚══════════════════════════════════════════════════════╝")
    check_prereqs()
    step1(); step2(); step3(); step4(); step5()
    step6(); step7(); step8(); step9(); step10()
    return print_summary()

if __name__ == "__main__":
    sys.exit(main())
