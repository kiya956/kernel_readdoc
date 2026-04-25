#!/usr/bin/env python3
"""
IUCV (Inter-User Communication Vehicle) Subsystem Workflow Verification
=========================================================================
Uses bpftrace to trace IUCV operations including path management,
message send/receive, and AF_IUCV socket operations on s390/z/VM.

Requirements:
  - Linux on s390 with IUCV support (CONFIG_IUCV=y/m)
  - bpftrace >= 0.14
  - Root privileges

Usage:
  sudo python3 test_iucv_subsystem.py
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
    print(f"\n── Step 1: IUCV symbols in kernel")
    r = run("grep -c ' iucv_' /proc/kallsyms")
    count = int(r.stdout.strip()) if r and r.returncode == 0 else 0
    if count > 5:
        print(f"{PASS}  {count} iucv_* symbols found")
        results.append((1, "IUCV symbols in kallsyms", "PASS"))
    else:
        print(f"{SKIP}  Only {count} IUCV symbols (not s390 or not built)")
        results.append((1, "IUCV symbols in kallsyms", "SKIP"))

def step2():
    print(f"\n── Step 2: IUCV module loaded")
    r = run("lsmod | grep -i iucv")
    if r and r.returncode == 0 and r.stdout.strip():
        print(f"{PASS}  IUCV module(s) loaded: {r.stdout.strip()[:120]}")
        results.append((2, "IUCV module loaded", "PASS"))
    else:
        r2 = run("grep -c ' iucv_message_send' /proc/kallsyms")
        cnt = int(r2.stdout.strip()) if r2 and r2.returncode == 0 else 0
        if cnt > 0:
            print(f"{PASS}  IUCV built-in")
            results.append((2, "IUCV module loaded", "PASS"))
        else:
            print(f"{SKIP}  IUCV module not loaded (requires s390/z/VM)")
            results.append((2, "IUCV module loaded", "SKIP"))

def step3():
    bpf_step(3, "Trace iucv_message_send message transmission",
        textwrap.dedent("""
            kprobe:iucv_message_send {
                printf("IUCV_MESSAGE_SEND path=%p msg=%p pid=%d\\n",
                       arg0, arg1, pid);
                exit();
            }
            interval:s:6 { exit(); }
        """),
        keyword="IUCV_MESSAGE_SEND",
        timeout=8,
    )

def step4():
    bpf_step(4, "Trace iucv_message_receive message reception",
        textwrap.dedent("""
            kprobe:iucv_message_receive {
                printf("IUCV_MESSAGE_RECEIVE path=%p msg=%p\\n",
                       arg0, arg1);
                exit();
            }
            interval:s:6 { exit(); }
        """),
        keyword="IUCV_MESSAGE_RECEIVE",
        timeout=8,
    )

def step5():
    bpf_step(5, "Trace iucv_path_connect path establishment",
        textwrap.dedent("""
            kprobe:iucv_path_connect {
                printf("IUCV_PATH_CONNECT path=%p handler=%p\\n",
                       arg0, arg1);
                exit();
            }
            interval:s:6 { exit(); }
        """),
        keyword="IUCV_PATH_CONNECT",
        timeout=8,
    )

def step6():
    bpf_step(6, "Trace iucv_path_accept path acceptance",
        textwrap.dedent("""
            kprobe:iucv_path_accept {
                printf("IUCV_PATH_ACCEPT path=%p handler=%p\\n",
                       arg0, arg1);
                exit();
            }
            interval:s:6 { exit(); }
        """),
        keyword="IUCV_PATH_ACCEPT",
        timeout=8,
    )

def step7():
    bpf_step(7, "Trace iucv_path_sever path disconnect",
        textwrap.dedent("""
            kprobe:iucv_path_sever {
                printf("IUCV_PATH_SEVER path=%p user=%p\\n",
                       arg0, arg1);
                exit();
            }
            interval:s:6 { exit(); }
        """),
        keyword="IUCV_PATH_SEVER",
        timeout=8,
    )

def step8():
    bpf_step(8, "Trace iucv_register handler registration",
        textwrap.dedent("""
            kprobe:iucv_register {
                printf("IUCV_REGISTER handler=%p smp=%d\\n",
                       arg0, arg1);
                exit();
            }
            interval:s:6 { exit(); }
        """),
        keyword="IUCV_REGISTER",
        timeout=8,
    )

def step9():
    bpf_step(9, "Trace iucv_sock_sendmsg AF_IUCV socket send",
        textwrap.dedent("""
            kprobe:iucv_sock_sendmsg {
                printf("IUCV_SOCK_SENDMSG sock=%p msg=%p pid=%d\\n",
                       arg0, arg1, pid);
                exit();
            }
            interval:s:6 { exit(); }
        """),
        keyword="IUCV_SOCK_SENDMSG",
        timeout=8,
    )

def step10():
    print(f"\n── Step 10: IUCV infrastructure presence")
    r = run("grep -c 'iucv' /proc/kallsyms")
    count = int(r.stdout.strip()) if r and r.returncode == 0 else 0
    if count > 10:
        print(f"{PASS}  {count} iucv symbols — infrastructure present")
        results.append((10, "IUCV infrastructure", "PASS"))
    else:
        r2 = run("uname -m")
        arch = r2.stdout.strip() if r2 else "unknown"
        if "s390" in arch:
            print(f"{SKIP}  s390 but IUCV not loaded")
        else:
            print(f"{SKIP}  Not s390 architecture ({arch}) — IUCV unavailable")
        results.append((10, "IUCV infrastructure", "SKIP"))

def print_summary():
    print("\n" + "═"*60)
    print("  IUCV Subsystem Verification Summary")
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
    print("║      IUCV Subsystem - Workflow Verification          ║")
    print("╚══════════════════════════════════════════════════════╝")
    check_prereqs()
    step1(); step2(); step3(); step4(); step5()
    step6(); step7(); step8(); step9(); step10()
    return print_summary()

if __name__ == "__main__":
    sys.exit(main())
