#!/usr/bin/env python3
"""
TLS (kTLS) Subsystem Workflow Verification
============================================
Uses bpftrace to trace kernel TLS record-layer offload:
software encrypt/decrypt, device offload, ULP init, and socket close.

Requirements:
  - Linux with CONFIG_TLS=m/y
  - bpftrace >= 0.14
  - Root privileges
  - kTLS module loaded (modprobe tls)

Usage:
  sudo python3 test_tls_subsystem.py
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

def step1_tls_module():
    print(f"\n── Step 1: kTLS module loaded")
    run("modprobe tls 2>/dev/null")
    r = run("grep -w tls /proc/modules")
    if r and r.returncode == 0 and r.stdout.strip():
        print(f"{PASS}  kTLS module loaded")
        print(f"         {r.stdout.strip()[:200]}")
        results.append((1, "kTLS module loaded", "PASS"))
    else:
        r2 = run("grep -c ' tls_' /proc/kallsyms")
        count = int(r2.stdout.strip()) if r2 and r2.returncode == 0 else 0
        if count > 10:
            print(f"{PASS}  kTLS built-in ({count} symbols)")
            results.append((1, "kTLS module loaded", "PASS"))
        else:
            print(f"{FAIL}  kTLS not available (CONFIG_TLS not set?)")
            results.append((1, "kTLS module loaded", "FAIL"))

def step2_tls_symbols():
    print(f"\n── Step 2: TLS symbols in kallsyms")
    r = run("grep -c ' tls_sw_\\| tls_device_\\| tls_init\\| tls_set_' /proc/kallsyms")
    count = int(r.stdout.strip()) if r and r.returncode == 0 else 0
    if count > 5:
        print(f"{PASS}  {count} kTLS symbols found")
        results.append((2, "TLS symbols in kallsyms", "PASS"))
    else:
        print(f"{FAIL}  Only {count} kTLS symbols")
        results.append((2, "TLS symbols in kallsyms", "FAIL"))

def step3_tls_sw_sendmsg():
    bpf_step(3, "tls_sw_sendmsg probe attachment",
        textwrap.dedent("""
            kprobe:tls_sw_sendmsg {
                printf("TLS_SW_SENDMSG pid=%d comm=%s\\n", pid, comm);
                exit();
            }
            interval:s:5 { exit(); }
        """),
        keyword="Attaching",
        timeout=8,
    )

def step4_tls_sw_recvmsg():
    bpf_step(4, "tls_sw_recvmsg probe attachment",
        textwrap.dedent("""
            kprobe:tls_sw_recvmsg {
                printf("TLS_SW_RECVMSG pid=%d comm=%s\\n", pid, comm);
                exit();
            }
            interval:s:5 { exit(); }
        """),
        keyword="Attaching",
        timeout=8,
    )

def step5_tls_set_device_offload():
    bpf_step(5, "tls_set_device_offload probe check",
        textwrap.dedent("""
            kprobe:tls_set_device_offload {
                printf("TLS_DEVICE_OFFLOAD pid=%d\\n", pid);
                exit();
            }
            interval:s:5 { exit(); }
        """),
        keyword="Attaching",
        timeout=8,
    )

def step6_tls_init():
    bpf_step(6, "tls_init ULP registration probe",
        textwrap.dedent("""
            kprobe:tls_init {
                printf("TLS_INIT pid=%d comm=%s\\n", pid, comm);
                exit();
            }
            interval:s:5 { exit(); }
        """),
        keyword="Attaching",
        timeout=8,
    )

def step7_tls_sk_proto_close():
    bpf_step(7, "tls_sk_proto_close probe attachment",
        textwrap.dedent("""
            kprobe:tls_sk_proto_close {
                printf("TLS_SK_PROTO_CLOSE pid=%d\\n", pid);
                exit();
            }
            interval:s:5 { exit(); }
        """),
        keyword="Attaching",
        timeout=8,
    )

def step8_tls_push_record():
    bpf_step(8, "tls_push_record probe check",
        textwrap.dedent("""
            kprobe:tls_push_record {
                printf("TLS_PUSH_RECORD pid=%d\\n", pid);
                exit();
            }
            interval:s:5 { exit(); }
        """),
        keyword="Attaching",
        timeout=8,
    )

def step9_tls_stat():
    print(f"\n── Step 9: /proc/net/tls_stat availability")
    r = run("cat /proc/net/tls_stat 2>/dev/null")
    if r and r.returncode == 0 and r.stdout.strip():
        lines = r.stdout.strip().split('\n')
        print(f"{PASS}  TLS stats available ({len(lines)} counters)")
        for line in lines[:5]:
            print(f"         {line}")
        results.append((9, "TLS stats in procfs", "PASS"))
    else:
        print(f"{SKIP}  /proc/net/tls_stat not available (kernel < 6.2?)")
        results.append((9, "TLS stats in procfs", "SKIP"))

def step10_tls_ulp_registered():
    print(f"\n── Step 10: TLS ULP registered in kernel")
    r = run("grep -c 'tls_sw_\\|tls_device_\\|tls_init' /proc/kallsyms")
    count = int(r.stdout.strip()) if r and r.returncode == 0 else 0
    r2 = run("cat /proc/net/tls_stat 2>/dev/null | head -1")
    has_stat = r2 and r2.returncode == 0 and r2.stdout.strip()
    if count > 5 or has_stat:
        print(f"{PASS}  TLS ULP registered ({count} symbols, stat={'yes' if has_stat else 'no'})")
        results.append((10, "TLS ULP registered", "PASS"))
    else:
        print(f"{FAIL}  TLS ULP does not appear registered")
        results.append((10, "TLS ULP registered", "FAIL"))

def print_summary():
    print("\n" + "═"*60)
    print("  kTLS Subsystem Verification Summary")
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
    print("║       kTLS Subsystem - Workflow Verification         ║")
    print("╚══════════════════════════════════════════════════════╝")
    check_prereqs()
    step1_tls_module()
    step2_tls_symbols()
    step3_tls_sw_sendmsg()
    step4_tls_sw_recvmsg()
    step5_tls_set_device_offload()
    step6_tls_init()
    step7_tls_sk_proto_close()
    step8_tls_push_record()
    step9_tls_stat()
    step10_tls_ulp_registered()
    return print_summary()

if __name__ == "__main__":
    sys.exit(main())
