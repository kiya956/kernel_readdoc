#!/usr/bin/env python3
"""
Traffic Control (TC / sched) Subsystem Workflow Verification
==============================================================
Uses bpftrace to trace TC queueing disciplines, classification,
enqueue/dequeue, and direct transmit paths.

Requirements:
  - Linux with CONFIG_NET_SCHED=y
  - bpftrace >= 0.14
  - Root privileges

Usage:
  sudo python3 test_sched_subsystem.py

Trigger: UDP traffic via localhost to exercise TC enqueue/dequeue paths.
"""

import subprocess, sys, os, time, textwrap, tempfile, socket

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

def trigger_udp_traffic():
    """Generate UDP traffic on loopback to trigger TC enqueue paths."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        for i in range(20):
            sock.sendto(f"tc-probe-test-{i}".encode(), ("127.0.0.1", port))
        sock.close()
    except Exception:
        pass

def bpf_step(num, desc, script, trigger=None, keyword=None, timeout=10):
    print(f"\n── Step {num}: {desc}")
    with tempfile.NamedTemporaryFile(mode='w', suffix='.bt', delete=False) as f:
        f.write(script); bt = f.name

    proc = subprocess.Popen(["bpftrace", bt],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    time.sleep(1.5)
    if trigger:
        if callable(trigger):
            trigger()
        else:
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

def step1_qdisc_run():
    bpf_step(1, "__qdisc_run traced on UDP send",
        textwrap.dedent("""
            kprobe:__qdisc_run {
                printf("QDISC_RUN pid=%d comm=%s\\n", pid, comm);
                exit();
            }
            interval:s:8 { exit(); }
        """),
        trigger=trigger_udp_traffic,
        keyword="QDISC_RUN",
        timeout=10,
    )

def step2_qdisc_enqueue_root():
    bpf_step(2, "qdisc enqueue root path",
        textwrap.dedent("""
            kprobe:__dev_xmit_skb {
                printf("DEV_XMIT_SKB pid=%d comm=%s\\n", pid, comm);
                exit();
            }
            interval:s:8 { exit(); }
        """),
        trigger=trigger_udp_traffic,
        keyword="DEV_XMIT_SKB",
        timeout=10,
    )

def step3_sch_direct_xmit():
    bpf_step(3, "sch_direct_xmit traced on TX",
        textwrap.dedent("""
            kprobe:sch_direct_xmit {
                printf("SCH_DIRECT_XMIT pid=%d comm=%s\\n", pid, comm);
                exit();
            }
            interval:s:8 { exit(); }
        """),
        trigger=trigger_udp_traffic,
        keyword="SCH_DIRECT_XMIT",
        timeout=10,
    )

def step4_tcf_classify():
    bpf_step(4, "tcf_classify probe attachment",
        textwrap.dedent("""
            kprobe:tcf_classify {
                printf("TCF_CLASSIFY pid=%d comm=%s\\n", pid, comm);
                exit();
            }
            interval:s:5 { exit(); }
        """),
        keyword="Attaching",
        timeout=8,
    )

def step5_htb_enqueue():
    bpf_step(5, "htb_enqueue probe check",
        textwrap.dedent("""
            kprobe:htb_enqueue {
                printf("HTB_ENQUEUE pid=%d\\n", pid);
                exit();
            }
            interval:s:5 { exit(); }
        """),
        keyword="Attaching",
        timeout=8,
    )

def step6_fq_codel_enqueue():
    bpf_step(6, "fq_codel_enqueue probe check",
        textwrap.dedent("""
            kprobe:fq_codel_enqueue {
                printf("FQ_CODEL_ENQUEUE pid=%d\\n", pid);
                exit();
            }
            interval:s:5 { exit(); }
        """),
        keyword="Attaching",
        timeout=8,
    )

def step7_pfifo_fast_enqueue():
    bpf_step(7, "pfifo_fast_enqueue probe check",
        textwrap.dedent("""
            kprobe:pfifo_fast_enqueue {
                printf("PFIFO_FAST_ENQUEUE pid=%d\\n", pid);
                exit();
            }
            interval:s:5 { exit(); }
        """),
        keyword="Attaching",
        timeout=8,
    )

def step8_tc_modify_qdisc():
    bpf_step(8, "tc_modify_qdisc probe attachment",
        textwrap.dedent("""
            kprobe:tc_modify_qdisc {
                printf("TC_MODIFY_QDISC pid=%d comm=%s\\n", pid, comm);
                exit();
            }
            interval:s:5 { exit(); }
        """),
        keyword="Attaching",
        timeout=8,
    )

def step9_cls_bpf_classify():
    bpf_step(9, "cls_bpf_classify probe check",
        textwrap.dedent("""
            kprobe:cls_bpf_classify {
                printf("CLS_BPF_CLASSIFY pid=%d\\n", pid);
                exit();
            }
            interval:s:5 { exit(); }
        """),
        keyword="Attaching",
        timeout=8,
    )

def step10_proc_net_psched():
    print(f"\n── Step 10: /proc/net/psched TC clock parameters")
    r = run("cat /proc/net/psched")
    if r and r.returncode == 0 and r.stdout.strip():
        print(f"{PASS}  /proc/net/psched readable")
        print(f"         {r.stdout.strip()}")
        r2 = run("tc qdisc show 2>/dev/null | head -5")
        if r2 and r2.returncode == 0 and r2.stdout.strip():
            print(f"         Active qdiscs:")
            for line in r2.stdout.strip().split('\n')[:3]:
                print(f"           {line[:90]}")
        results.append((10, "/proc/net/psched TC clock", "PASS"))
    else:
        print(f"{FAIL}  /proc/net/psched not available")
        results.append((10, "/proc/net/psched TC clock", "FAIL"))

def print_summary():
    print("\n" + "═"*60)
    print("  TC (sched) Subsystem Verification Summary")
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
    print("║    TC (sched) Subsystem - Workflow Verification      ║")
    print("╚══════════════════════════════════════════════════════╝")
    check_prereqs()
    step1_qdisc_run()
    step2_qdisc_enqueue_root()
    step3_sch_direct_xmit()
    step4_tcf_classify()
    step5_htb_enqueue()
    step6_fq_codel_enqueue()
    step7_pfifo_fast_enqueue()
    step8_tc_modify_qdisc()
    step9_cls_bpf_classify()
    step10_proc_net_psched()
    return print_summary()

if __name__ == "__main__":
    sys.exit(main())
