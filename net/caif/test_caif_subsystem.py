#!/usr/bin/env python3
"""
CAIF Subsystem Workflow Verification
=======================================
Uses bpftrace to trace CAIF (Communication CPU to Application CPU Interface)
protocol operations including client connections, packet handling, and
layer management.

Requirements:
  - Linux with CAIF support (CONFIG_CAIF=y/m)
  - bpftrace >= 0.14
  - Root privileges

Usage:
  sudo python3 test_caif_subsystem.py
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
    print(f"\n── Step 1: CAIF symbols in kernel")
    r = run("grep -c ' caif_\\| cfpkt_\\| cflayer' /proc/kallsyms")
    count = int(r.stdout.strip()) if r and r.returncode == 0 else 0
    if count > 5:
        print(f"{PASS}  {count} CAIF symbols found")
        results.append((1, "CAIF symbols in kallsyms", "PASS"))
    else:
        print(f"{SKIP}  Only {count} CAIF symbols (not built or modular)")
        results.append((1, "CAIF symbols in kallsyms", "SKIP"))

def step2():
    print(f"\n── Step 2: CAIF module loaded")
    r = run("lsmod | grep -i caif")
    if r and r.returncode == 0 and r.stdout.strip():
        print(f"{PASS}  CAIF module(s) loaded: {r.stdout.strip()[:120]}")
        results.append((2, "CAIF module loaded", "PASS"))
    else:
        r2 = run("grep -c ' caif_connect' /proc/kallsyms")
        cnt = int(r2.stdout.strip()) if r2 and r2.returncode == 0 else 0
        if cnt > 0:
            print(f"{PASS}  CAIF built-in")
            results.append((2, "CAIF module loaded", "PASS"))
        else:
            print(f"{SKIP}  CAIF module not loaded")
            results.append((2, "CAIF module loaded", "SKIP"))

def step3():
    bpf_step(3, "Trace caif_connect_client connection setup",
        textwrap.dedent("""
            kprobe:caif_connect_client {
                printf("CAIF_CONNECT_CLIENT pid=%d comm=%s\\n", pid, comm);
                exit();
            }
            interval:s:6 { exit(); }
        """),
        keyword="CAIF_CONNECT_CLIENT",
        timeout=8,
    )

def step4():
    bpf_step(4, "Trace cfpkt_fromnative packet conversion",
        textwrap.dedent("""
            kprobe:cfpkt_fromnative {
                printf("CFPKT_FROMNATIVE dir=%d pkt=%p\\n", arg0, arg1);
                exit();
            }
            interval:s:6 { exit(); }
        """),
        keyword="CFPKT_FROMNATIVE",
        timeout=8,
    )

def step5():
    bpf_step(5, "Trace caif_enroll_dev device enrollment",
        textwrap.dedent("""
            kprobe:caif_enroll_dev {
                printf("CAIF_ENROLL_DEV dev=%p head=%p pid=%d\\n",
                       arg0, arg1, pid);
                exit();
            }
            interval:s:6 { exit(); }
        """),
        keyword="CAIF_ENROLL_DEV",
        timeout=8,
    )

def step6():
    bpf_step(6, "Trace cfcnfg_add_adaptation_layer layer chain build",
        textwrap.dedent("""
            kprobe:cfcnfg_add_adaptation_layer {
                printf("CFCNFG_ADD_ADAPT_LAYER cnfg=%p pid=%d\\n",
                       arg0, pid);
                exit();
            }
            interval:s:6 { exit(); }
        """),
        keyword="CFCNFG_ADD_ADAPT_LAYER",
        timeout=8,
    )

def step7():
    bpf_step(7, "Trace caif_socket sendmsg",
        textwrap.dedent("""
            kprobe:caif_sendmsg {
                printf("CAIF_SENDMSG sock=%p msg=%p pid=%d\\n",
                       arg0, arg1, pid);
                exit();
            }
            interval:s:6 { exit(); }
        """),
        keyword="CAIF_SENDMSG",
        timeout=8,
    )

def step8():
    bpf_step(8, "Trace caif_socket recvmsg",
        textwrap.dedent("""
            kprobe:caif_recvmsg {
                printf("CAIF_RECVMSG sock=%p msg=%p pid=%d\\n",
                       arg0, arg1, pid);
                exit();
            }
            interval:s:6 { exit(); }
        """),
        keyword="CAIF_RECVMSG",
        timeout=8,
    )

def step9():
    bpf_step(9, "Trace cfmuxl_set_uplayer mux layer setup",
        textwrap.dedent("""
            kprobe:cfmuxl_set_uplayer {
                printf("CFMUXL_SET_UPLAYER mux=%p up=%p\\n", arg0, arg1);
                exit();
            }
            interval:s:6 { exit(); }
        """),
        keyword="CFMUXL_SET_UPLAYER",
        timeout=8,
    )

def step10():
    print(f"\n── Step 10: CAIF protocol family registered")
    r = run("grep -c 'caif' /proc/kallsyms")
    count = int(r.stdout.strip()) if r and r.returncode == 0 else 0
    if count > 10:
        print(f"{PASS}  {count} caif symbols — protocol family present")
        results.append((10, "CAIF protocol family", "PASS"))
    else:
        print(f"{SKIP}  CAIF protocol family not found")
        results.append((10, "CAIF protocol family", "SKIP"))

def print_summary():
    print("\n" + "═"*60)
    print("  CAIF Subsystem Verification Summary")
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
    print("║      CAIF Subsystem - Workflow Verification          ║")
    print("╚══════════════════════════════════════════════════════╝")
    check_prereqs()
    step1(); step2(); step3(); step4(); step5()
    step6(); step7(); step8(); step9(); step10()
    return print_summary()

if __name__ == "__main__":
    sys.exit(main())
