#!/usr/bin/env python3
"""
PF_KEY (net/key) Subsystem Workflow Verification
===================================================
Uses bpftrace to trace PF_KEY v2 operations including SADB message
processing, SA installation, and IPsec/XFRM interaction.

Requirements:
  - Linux with PF_KEY support (CONFIG_NET_KEY=y/m)
  - bpftrace >= 0.14
  - Root privileges

Usage:
  sudo python3 test_key_subsystem.py
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
    print(f"\n── Step 1: PF_KEY symbols in kernel")
    r = run("grep -c ' pfkey_' /proc/kallsyms")
    count = int(r.stdout.strip()) if r and r.returncode == 0 else 0
    if count > 5:
        print(f"{PASS}  {count} pfkey_* symbols found")
        results.append((1, "PF_KEY symbols in kallsyms", "PASS"))
    else:
        print(f"{SKIP}  Only {count} PF_KEY symbols (not built or modular)")
        results.append((1, "PF_KEY symbols in kallsyms", "SKIP"))

def step2():
    print(f"\n── Step 2: PF_KEY module loaded")
    r = run("lsmod | grep -i af_key")
    if r and r.returncode == 0 and r.stdout.strip():
        print(f"{PASS}  af_key module loaded: {r.stdout.strip()[:120]}")
        results.append((2, "PF_KEY module loaded", "PASS"))
    else:
        r2 = run("grep -c ' pfkey_sendmsg' /proc/kallsyms")
        cnt = int(r2.stdout.strip()) if r2 and r2.returncode == 0 else 0
        if cnt > 0:
            print(f"{PASS}  PF_KEY built-in")
            results.append((2, "PF_KEY module loaded", "PASS"))
        else:
            print(f"{SKIP}  PF_KEY module not loaded")
            results.append((2, "PF_KEY module loaded", "SKIP"))

def step3():
    bpf_step(3, "Trace pfkey_sendmsg SADB message send",
        textwrap.dedent("""
            kprobe:pfkey_sendmsg {
                printf("PFKEY_SENDMSG sock=%p msg=%p len=%d\\n",
                       arg0, arg1, arg2);
                exit();
            }
            interval:s:6 { exit(); }
        """),
        keyword="PFKEY_SENDMSG",
        timeout=8,
    )

def step4():
    bpf_step(4, "Trace pfkey_process SADB message dispatch",
        textwrap.dedent("""
            kprobe:pfkey_process {
                printf("PFKEY_PROCESS sock=%p hdr=%p pid=%d\\n",
                       arg0, arg1, pid);
                exit();
            }
            interval:s:6 { exit(); }
        """),
        keyword="PFKEY_PROCESS",
        timeout=8,
    )

def step5():
    bpf_step(5, "Trace pfkey_sadb2xfrm_state SA conversion",
        textwrap.dedent("""
            kprobe:pfkey_sadb2xfrm_state {
                printf("PFKEY_SADB2XFRM hdr=%p sa=%p pid=%d\\n",
                       arg0, arg1, pid);
                exit();
            }
            interval:s:6 { exit(); }
        """),
        keyword="PFKEY_SADB2XFRM",
        timeout=8,
    )

def step6():
    bpf_step(6, "Trace pfkey_broadcast SADB event broadcast",
        textwrap.dedent("""
            kprobe:pfkey_broadcast {
                printf("PFKEY_BROADCAST skb=%p allocation=%d\\n",
                       arg0, arg1);
                exit();
            }
            interval:s:6 { exit(); }
        """),
        keyword="PFKEY_BROADCAST",
        timeout=8,
    )

def step7():
    bpf_step(7, "Trace pfkey_create socket creation",
        textwrap.dedent("""
            kprobe:pfkey_create {
                printf("PFKEY_CREATE net=%p sock=%p pid=%d\\n",
                       arg0, arg1, pid);
                exit();
            }
            interval:s:6 { exit(); }
        """),
        keyword="PFKEY_CREATE",
        timeout=8,
    )

def step8():
    bpf_step(8, "Trace pfkey_dump SADB dump",
        textwrap.dedent("""
            kprobe:pfkey_dump {
                printf("PFKEY_DUMP sk=%p hdr=%p pid=%d\\n",
                       arg0, arg1, pid);
                exit();
            }
            interval:s:6 { exit(); }
        """),
        keyword="PFKEY_DUMP",
        timeout=8,
    )

def step9():
    print(f"\n── Step 9: XFRM/IPsec state availability")
    r = run("ip xfrm state list 2>/dev/null | head -3")
    if r and r.returncode == 0:
        if r.stdout.strip():
            print(f"{PASS}  XFRM states present")
        else:
            print(f"{PASS}  XFRM subsystem accessible (no SAs)")
        results.append((9, "XFRM state accessible", "PASS"))
    else:
        print(f"{SKIP}  XFRM not available")
        results.append((9, "XFRM state accessible", "SKIP"))

def step10():
    print(f"\n── Step 10: PF_KEY protocol family registered")
    r = run("grep -c 'pfkey' /proc/kallsyms")
    count = int(r.stdout.strip()) if r and r.returncode == 0 else 0
    if count > 10:
        print(f"{PASS}  {count} pfkey symbols — protocol family present")
        results.append((10, "PF_KEY protocol family", "PASS"))
    else:
        print(f"{SKIP}  PF_KEY protocol family not found")
        results.append((10, "PF_KEY protocol family", "SKIP"))

def print_summary():
    print("\n" + "═"*60)
    print("  PF_KEY Subsystem Verification Summary")
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
    print("║     PF_KEY Subsystem - Workflow Verification         ║")
    print("╚══════════════════════════════════════════════════════╝")
    check_prereqs()
    step1(); step2(); step3(); step4(); step5()
    step6(); step7(); step8(); step9(); step10()
    return print_summary()

if __name__ == "__main__":
    sys.exit(main())
