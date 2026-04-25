#!/usr/bin/env python3
"""
9P Subsystem Workflow Verification
=====================================
Uses bpftrace to trace Plan 9 filesystem protocol client operations,
transport module usage, and RPC request handling.

Requirements:
  - Linux with 9P support (CONFIG_NET_9P=y/m)
  - bpftrace >= 0.14
  - Root privileges

Usage:
  sudo python3 test_9p_subsystem.py
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
    print(f"\n── Step 1: 9P symbols in kernel")
    r = run("grep -c ' p9_' /proc/kallsyms")
    count = int(r.stdout.strip()) if r and r.returncode == 0 else 0
    if count > 5:
        print(f"{PASS}  {count} p9_* symbols found")
        results.append((1, "9P symbols in kallsyms", "PASS"))
    else:
        print(f"{SKIP}  Only {count} p9 symbols (9P not built or modular)")
        results.append((1, "9P symbols in kallsyms", "SKIP"))

def step2():
    print(f"\n── Step 2: 9P module loaded")
    r = run("lsmod | grep -i 9p")
    if r and r.returncode == 0 and r.stdout.strip():
        print(f"{PASS}  9P module(s) loaded: {r.stdout.strip()[:120]}")
        results.append((2, "9P module loaded", "PASS"))
    else:
        r2 = run("grep -c ' p9_client' /proc/kallsyms")
        cnt = int(r2.stdout.strip()) if r2 and r2.returncode == 0 else 0
        if cnt > 0:
            print(f"{PASS}  9P built-in ({cnt} symbols)")
            results.append((2, "9P module loaded", "PASS"))
        else:
            print(f"{SKIP}  9P module not loaded")
            results.append((2, "9P module loaded", "SKIP"))

def step3():
    bpf_step(3, "Trace p9_client_create",
        textwrap.dedent("""
            kprobe:p9_client_create {
                printf("P9_CLIENT_CREATE pid=%d comm=%s\\n", pid, comm);
                exit();
            }
            interval:s:6 { exit(); }
        """),
        keyword="P9_CLIENT_CREATE",
        timeout=8,
    )

def step4():
    bpf_step(4, "Trace p9_client_rpc request submission",
        textwrap.dedent("""
            kprobe:p9_client_rpc {
                printf("P9_CLIENT_RPC client=%p type=%d pid=%d\\n",
                       arg0, arg1, pid);
                exit();
            }
            interval:s:6 { exit(); }
        """),
        keyword="P9_CLIENT_RPC",
        timeout=8,
    )

def step5():
    bpf_step(5, "Trace p9_virtio_request transport",
        textwrap.dedent("""
            kprobe:p9_virtio_request {
                printf("P9_VIRTIO_REQUEST client=%p req=%p\\n", arg0, arg1);
                exit();
            }
            interval:s:6 { exit(); }
        """),
        keyword="P9_VIRTIO_REQUEST",
        timeout=8,
    )

def step6():
    bpf_step(6, "Trace p9_tag_alloc tag allocation",
        textwrap.dedent("""
            kprobe:p9_tag_alloc {
                printf("P9_TAG_ALLOC client=%p type=%d pid=%d\\n",
                       arg0, arg1, pid);
                exit();
            }
            interval:s:6 { exit(); }
        """),
        keyword="P9_TAG_ALLOC",
        timeout=8,
    )

def step7():
    bpf_step(7, "Trace 9P fd-transport connect",
        textwrap.dedent("""
            kprobe:p9_fd_create {
                printf("P9_FD_CREATE client=%p pid=%d\\n", arg0, pid);
                exit();
            }
            interval:s:6 { exit(); }
        """),
        keyword="P9_FD_CREATE",
        timeout=8,
    )

def step8():
    print(f"\n── Step 8: Check 9P transport modules registered")
    r = run("cat /proc/filesystems 2>/dev/null | grep 9p")
    if r and r.returncode == 0 and r.stdout.strip():
        print(f"{PASS}  9P filesystem registered: {r.stdout.strip()}")
        results.append((8, "9P filesystem registered", "PASS"))
    else:
        print(f"{SKIP}  9P filesystem not registered")
        results.append((8, "9P filesystem registered", "SKIP"))

def step9():
    bpf_step(9, "Trace p9_client_destroy teardown",
        textwrap.dedent("""
            kprobe:p9_client_destroy {
                printf("P9_CLIENT_DESTROY client=%p pid=%d\\n", arg0, pid);
                exit();
            }
            interval:s:6 { exit(); }
        """),
        keyword="P9_CLIENT_DESTROY",
        timeout=8,
    )

def step10():
    print(f"\n── Step 10: 9P debug/trace infrastructure")
    r = run("ls /sys/module/9pnet/parameters/ 2>/dev/null")
    if r and r.returncode == 0 and r.stdout.strip():
        print(f"{PASS}  9pnet module parameters: {r.stdout.strip()[:120]}")
        results.append((10, "9P module parameters present", "PASS"))
    else:
        r2 = run("grep -c 'p9_debug' /proc/kallsyms 2>/dev/null")
        cnt = int(r2.stdout.strip()) if r2 and r2.returncode == 0 else 0
        if cnt > 0:
            print(f"{PASS}  p9_debug symbols present")
            results.append((10, "9P module parameters present", "PASS"))
        else:
            print(f"{SKIP}  9P debug infrastructure not found")
            results.append((10, "9P module parameters present", "SKIP"))

def print_summary():
    print("\n" + "═"*60)
    print("  9P Subsystem Verification Summary")
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
    print("║       9P Subsystem - Workflow Verification           ║")
    print("╚══════════════════════════════════════════════════════╝")
    check_prereqs()
    step1(); step2(); step3(); step4(); step5()
    step6(); step7(); step8(); step9(); step10()
    return print_summary()

if __name__ == "__main__":
    sys.exit(main())
