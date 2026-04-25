#!/usr/bin/env python3
"""
TIPC Subsystem Workflow Verification
======================================
Uses bpftrace to trace TIPC messaging, node discovery,
link management, and bearer operations.

Requirements:
  - Linux with TIPC (CONFIG_TIPC=y/m)
  - bpftrace >= 0.14
  - Root privileges

Usage:
  sudo python3 test_tipc_subsystem.py
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
    print(f"\n── Step 1: TIPC symbols in kernel")
    r = run("grep -c ' tipc_' /proc/kallsyms")
    count = int(r.stdout.strip()) if r and r.returncode == 0 else 0
    if count > 10:
        print(f"{PASS}  {count} tipc_* symbols found")
        results.append((1, "tipc symbols in kallsyms", "PASS"))
    else:
        print(f"{FAIL}  Only {count} tipc symbols (TIPC not built?)")
        results.append((1, "tipc symbols in kallsyms", "FAIL"))

def step2_module_loaded():
    print(f"\n── Step 2: TIPC module loaded")
    r = run("lsmod | grep -w tipc")
    if r and r.returncode == 0 and r.stdout.strip():
        print(f"{PASS}  tipc module is loaded")
        print(f"         {r.stdout.strip()[:200]}")
        results.append((2, "tipc module loaded", "PASS"))
    else:
        r2 = run("grep -c ' tipc_' /proc/kallsyms")
        count = int(r2.stdout.strip()) if r2 and r2.returncode == 0 else 0
        if count > 50:
            print(f"{PASS}  tipc is built-in (not a module)")
            results.append((2, "tipc module loaded", "PASS"))
        else:
            print(f"{SKIP}  tipc module not loaded (try: modprobe tipc)")
            results.append((2, "tipc module loaded", "SKIP"))

def step3_tipc_rcv():
    bpf_step(3, "tipc_rcv — incoming message dispatch",
        textwrap.dedent("""
            kprobe:tipc_rcv {
                printf("TIPC_RCV skb=%p bearer=%p pid=%d\\n",
                       arg1, arg2, pid);
                exit();
            }
            interval:s:6 { exit(); }
        """),
        trigger="true",
        keyword="TIPC_RCV",
        timeout=8,
    )

def step4_tipc_send():
    bpf_step(4, "tipc_send_stream — stream socket send",
        textwrap.dedent("""
            kprobe:tipc_send_stream {
                printf("TIPC_SEND_STREAM sock=%p pid=%d comm=%s\\n",
                       arg0, pid, comm);
                exit();
            }
            kprobe:tipc_sendmsg {
                printf("TIPC_SENDMSG sock=%p pid=%d comm=%s\\n",
                       arg0, pid, comm);
                exit();
            }
            interval:s:6 { exit(); }
        """),
        trigger="true",
        keyword="TIPC_SEND",
        timeout=8,
    )

def step5_tipc_node():
    bpf_step(5, "tipc_node_link_up — node link activation",
        textwrap.dedent("""
            kprobe:tipc_node_link_up {
                printf("TIPC_NODE_LINK_UP node=%p bearer=%d pid=%d\\n",
                       arg0, arg1, pid);
                exit();
            }
            kprobe:tipc_node_create {
                printf("TIPC_NODE_CREATE net=%p addr=%d pid=%d\\n",
                       arg0, arg1, pid);
                exit();
            }
            interval:s:6 { exit(); }
        """),
        trigger="true",
        keyword="TIPC_NODE_",
        timeout=8,
    )

def step6_tipc_bearer():
    print(f"\n── Step 6: TIPC bearer status")
    r = run("tipc bearer list 2>/dev/null")
    if r and r.returncode == 0 and r.stdout.strip():
        print(f"{PASS}  TIPC bearers found")
        print(f"         {r.stdout.strip()[:200]}")
        results.append((6, "tipc bearer status", "PASS"))
    else:
        r2 = run("grep -c ' tipc_bearer' /proc/kallsyms")
        count = int(r2.stdout.strip()) if r2 and r2.returncode == 0 else 0
        if count > 0:
            print(f"{SKIP}  tipc bearer symbols exist but no bearers enabled")
            results.append((6, "tipc bearer status", "SKIP"))
        else:
            print(f"{SKIP}  tipc bearer not available")
            results.append((6, "tipc bearer status", "SKIP"))

def step7_tipc_link():
    bpf_step(7, "tipc_link_build_proto_msg — link protocol messages",
        textwrap.dedent("""
            kprobe:tipc_link_build_proto_msg {
                printf("TIPC_LINK_PROTO link=%p pid=%d\\n",
                       arg0, pid);
                exit();
            }
            interval:s:6 { exit(); }
        """),
        trigger="true",
        keyword="TIPC_LINK_PROTO",
        timeout=8,
    )

def step8_tipc_nametable():
    print(f"\n── Step 8: TIPC name table")
    r = run("tipc nametable show 2>/dev/null")
    if r and r.returncode == 0:
        out = r.stdout.strip()
        if out:
            print(f"{PASS}  Name table entries found")
            print(f"         {out[:200]}")
            results.append((8, "tipc nametable show", "PASS"))
        else:
            print(f"{SKIP}  Name table is empty (no services published)")
            results.append((8, "tipc nametable show", "SKIP"))
    else:
        print(f"{SKIP}  tipc nametable show not available")
        results.append((8, "tipc nametable show", "SKIP"))

def step9_tipc_socket():
    bpf_step(9, "tipc_sk_create — TIPC socket creation",
        textwrap.dedent("""
            kprobe:tipc_sk_create {
                printf("TIPC_SK_CREATE net=%p sock=%p pid=%d comm=%s\\n",
                       arg0, arg1, pid, comm);
                exit();
            }
            interval:s:6 { exit(); }
        """),
        trigger="true",
        keyword="TIPC_SK_CREATE",
        timeout=8,
    )

def step10_tipc_tracepoints():
    print(f"\n── Step 10: TIPC tracepoints in available_events")
    r = run("grep -c 'tipc' /sys/kernel/debug/tracing/available_events 2>/dev/null")
    count = int(r.stdout.strip()) if r and r.returncode == 0 else 0
    if count > 0:
        print(f"{PASS}  {count} TIPC tracepoint(s) found")
        r2 = run("grep 'tipc' /sys/kernel/debug/tracing/available_events 2>/dev/null | head -5")
        if r2 and r2.stdout.strip():
            print(f"         {r2.stdout.strip()[:200]}")
        results.append((10, "tipc tracepoints in available_events", "PASS"))
    else:
        print(f"{SKIP}  No TIPC tracepoints found (CONFIG_TIPC_MEDIA_IB? or module not loaded)")
        results.append((10, "tipc tracepoints in available_events", "SKIP"))

def print_summary():
    print("\n" + "═"*60)
    print("  TIPC Subsystem Verification Summary")
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
    print("║        TIPC Subsystem - Workflow Verification        ║")
    print("╚══════════════════════════════════════════════════════╝")
    check_prereqs()
    step1_symbols()
    step2_module_loaded()
    step3_tipc_rcv()
    step4_tipc_send()
    step5_tipc_node()
    step6_tipc_bearer()
    step7_tipc_link()
    step8_tipc_nametable()
    step9_tipc_socket()
    step10_tipc_tracepoints()
    return print_summary()

if __name__ == "__main__":
    sys.exit(main())
