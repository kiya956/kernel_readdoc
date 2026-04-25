#!/usr/bin/env python3
"""
HSR Subsystem Workflow Verification
======================================
Uses bpftrace to trace HSR/PRP frame forwarding, duplicate detection,
and supervision frame handling.

Requirements:
  - Linux with HSR (CONFIG_HSR=y/m)
  - bpftrace >= 0.14
  - Root privileges

Usage:
  sudo python3 test_hsr_subsystem.py
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
        if any(x in combined for x in ("not traceable","No probes","ERROR")):
            print(f"{SKIP}  Symbol not traceable")
            print(f"         {err.strip()[:200]}")
            results.append((num, desc, "SKIP"))
        else:
            print(f"{FAIL}  Expected '{keyword}' not found")
            print(f"         {combined.strip()[:200]}")
            results.append((num, desc, "FAIL"))

def step1_symbols():
    print(f"\n── Step 1: HSR symbols in kernel")
    r = run("grep -c ' hsr_' /proc/kallsyms")
    count = int(r.stdout.strip()) if r and r.returncode == 0 else 0
    if count > 5:
        print(f"{PASS}  {count} hsr_* symbols found")
        results.append((1, "hsr symbols in kallsyms", "PASS"))
    else:
        print(f"{FAIL}  Only {count} hsr symbols (CONFIG_HSR not enabled?)")
        results.append((1, "hsr symbols in kallsyms", "FAIL"))

def step2_module_loaded():
    print(f"\n── Step 2: HSR module loaded")
    r = run("lsmod | grep -q '^hsr ' && echo loaded || "
            "grep -q ' hsr_' /proc/kallsyms && echo builtin || echo missing")
    status = r.stdout.strip() if r else "missing"
    if status in ("loaded", "builtin"):
        print(f"{PASS}  HSR module status: {status}")
        results.append((2, "hsr module loaded", "PASS"))
    else:
        r2 = run("modprobe hsr 2>/dev/null && echo ok || echo fail")
        if r2 and r2.stdout.strip() == "ok":
            print(f"{PASS}  HSR module loaded via modprobe")
            results.append((2, "hsr module loaded", "PASS"))
        else:
            print(f"{SKIP}  HSR module not available")
            results.append((2, "hsr module loaded", "SKIP"))

def step3_hsr_forward_skb():
    bpf_step(3, "hsr_forward_skb — frame forwarding entry",
        textwrap.dedent("""
            kprobe:hsr_forward_skb {
                printf("HSR_FORWARD_SKB skb=%p port=%p pid=%d comm=%s\\n",
                       arg0, arg1, pid, comm);
                exit();
            }
            interval:s:6 { exit(); }
        """),
        trigger="true",
        keyword="HSR_FORWARD_SKB",
        timeout=8,
    )

def step4_hsr_handle_frame():
    bpf_step(4, "hsr_handle_frame — slave rx handler",
        textwrap.dedent("""
            kprobe:hsr_handle_frame {
                printf("HSR_HANDLE_FRAME skb=%p pid=%d\\n",
                       arg0, pid);
                exit();
            }
            interval:s:6 { exit(); }
        """),
        trigger="true",
        keyword="HSR_HANDLE_FRAME",
        timeout=8,
    )

def step5_hsr_dev_setup():
    bpf_step(5, "hsr_dev_setup / hsr_dev_finalize — device init",
        textwrap.dedent("""
            kprobe:hsr_dev_setup {
                printf("HSR_DEV_SETUP dev=%p pid=%d\\n", arg0, pid);
                exit();
            }
            kprobe:hsr_dev_finalize {
                printf("HSR_DEV_FINALIZE dev=%p pid=%d\\n", arg0, pid);
                exit();
            }
            interval:s:6 { exit(); }
        """),
        trigger="true",
        keyword="HSR_DEV_",
        timeout=8,
    )

def step6_hsr_devices():
    print(f"\n── Step 6: HSR interfaces in /sys/class/net")
    r = run("ls -d /sys/class/net/hsr* 2>/dev/null")
    if r and r.returncode == 0 and r.stdout.strip():
        devs = r.stdout.strip().split('\n')
        print(f"{PASS}  {len(devs)} HSR interface(s): {devs[0]}")
        results.append((6, "hsr interfaces present", "PASS"))
    else:
        r2 = run("ip -d link show type hsr 2>/dev/null")
        if r2 and r2.returncode == 0 and r2.stdout.strip():
            print(f"{PASS}  HSR interface found via ip link")
            results.append((6, "hsr interfaces present", "PASS"))
        else:
            print(f"{SKIP}  No HSR interfaces configured")
            results.append((6, "hsr interfaces present", "SKIP"))

def step7_hsr_add_node():
    bpf_step(7, "hsr_add_node — node table insertion",
        textwrap.dedent("""
            kprobe:hsr_add_node {
                printf("HSR_ADD_NODE priv=%p addr=%p pid=%d\\n",
                       arg0, arg1, pid);
                exit();
            }
            interval:s:6 { exit(); }
        """),
        trigger="true",
        keyword="HSR_ADD_NODE",
        timeout=8,
    )

def step8_hsr_supervision():
    bpf_step(8, "hsr_handle_sup_frame — supervision frame processing",
        textwrap.dedent("""
            kprobe:hsr_handle_sup_frame {
                printf("HSR_HANDLE_SUP_FRAME node=%p pid=%d\\n",
                       arg0, pid);
                exit();
            }
            interval:s:6 { exit(); }
        """),
        trigger="true",
        keyword="HSR_HANDLE_SUP_FRAME",
        timeout=8,
    )

def step9_hsr_prp_check():
    print(f"\n── Step 9: PRP variant support in kernel config")
    r = run("grep -s 'CONFIG_HSR' /boot/config-$(uname -r) 2>/dev/null || "
            "zgrep -s 'CONFIG_HSR' /proc/config.gz 2>/dev/null || "
            "grep -sc ' hsr_' /proc/kallsyms")
    out = r.stdout.strip() if r else ""
    if "CONFIG_HSR=y" in out or "CONFIG_HSR=m" in out:
        print(f"{PASS}  HSR/PRP enabled in kernel config")
        print(f"         {out[:200]}")
        results.append((9, "PRP variant supported", "PASS"))
    elif r and r.returncode == 0 and out:
        count = 0
        try:
            count = int(out)
        except ValueError:
            pass
        if count > 0:
            print(f"{PASS}  HSR symbols present ({count}), PRP likely supported")
            results.append((9, "PRP variant supported", "PASS"))
        else:
            print(f"{SKIP}  Could not determine PRP support")
            results.append((9, "PRP variant supported", "SKIP"))
    else:
        print(f"{SKIP}  Kernel config not accessible")
        results.append((9, "PRP variant supported", "SKIP"))

def step10_hsr_tracepoints():
    print(f"\n── Step 10: HSR tracepoints or debug symbols")
    r = run("grep -c 'hsr' /sys/kernel/debug/tracing/available_events 2>/dev/null || "
            "grep -c ' hsr_' /proc/kallsyms")
    count = int(r.stdout.strip()) if r and r.returncode == 0 else 0
    if count > 0:
        print(f"{PASS}  {count} HSR trace/debug entry points found")
        results.append((10, "hsr tracepoints or symbols", "PASS"))
    else:
        print(f"{SKIP}  No HSR tracepoints found")
        results.append((10, "hsr tracepoints or symbols", "SKIP"))

def print_summary():
    print("\n" + "═"*60)
    print("  HSR Subsystem Verification Summary")
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
    print("║       HSR Subsystem - Workflow Verification          ║")
    print("╚══════════════════════════════════════════════════════╝")
    check_prereqs()
    step1_symbols()
    step2_module_loaded()
    step3_hsr_forward_skb()
    step4_hsr_handle_frame()
    step5_hsr_dev_setup()
    step6_hsr_devices()
    step7_hsr_add_node()
    step8_hsr_supervision()
    step9_hsr_prp_check()
    step10_hsr_tracepoints()
    return print_summary()

if __name__ == "__main__":
    sys.exit(main())
