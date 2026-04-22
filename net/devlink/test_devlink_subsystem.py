#!/usr/bin/env python3
"""
devlink Subsystem Workflow Verification
=========================================
Uses bpftrace to trace devlink device registration, netlink command
handling, and health/port operations.

Requirements:
  - Linux with devlink (CONFIG_NET_DEVLINK=y)
  - bpftrace >= 0.14
  - Root privileges
  - At least one devlink-capable NIC (mlx5, ice, bnxt, …)

Usage:
  sudo python3 test_devlink_subsystem.py
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
    print(f"\n── Step 1: devlink symbols in kernel")
    r = run("grep -c ' devlink_' /proc/kallsyms")
    count = int(r.stdout.strip()) if r and r.returncode == 0 else 0
    if count > 20:
        print(f"{PASS}  {count} devlink_* symbols")
        results.append((1, "devlink symbols in kallsyms", "PASS"))
    else:
        print(f"{FAIL}  Only {count} devlink symbols (devlink not built?)")
        results.append((1, "devlink symbols in kallsyms", "FAIL"))

def step2_devlink_devices():
    print(f"\n── Step 2: devlink devices registered")
    r = run("devlink dev show 2>/dev/null")
    if r and r.returncode == 0 and r.stdout.strip():
        devs = r.stdout.strip().split('\n')
        print(f"{PASS}  {len(devs)} devlink device(s): {devs[0]}")
        results.append((2, "devlink devices present", "PASS"))
    else:
        print(f"{SKIP}  No devlink devices found (no compatible NIC?)")
        results.append((2, "devlink devices present", "SKIP"))

def step3_devlink_register():
    bpf_step(3, "devlink_register called when driver loads",
        textwrap.dedent("""
            kprobe:devlink_register {
                printf("DEVLINK_REGISTER devlink=%p pid=%d comm=%s\\n",
                       arg0, pid, comm);
                exit();
            }
            interval:s:6 { exit(); }
        """),
        trigger="true",
        keyword="DEVLINK_REGISTER",
        timeout=8,
    )

def step4_devlink_nl_cmd_get():
    bpf_step(4, "devlink netlink GET command handled",
        textwrap.dedent("""
            kprobe:devlink_nl_cmd_get_doit {
                printf("DEVLINK_NL_GET_DOIT info=%p pid=%d\\n",
                       arg1, pid);
                exit();
            }
            interval:s:8 { exit(); }
        """),
        trigger="devlink dev show 2>/dev/null; true",
        keyword="DEVLINK_NL_GET_DOIT",
        timeout=10,
    )

def step5_devlink_port_get():
    bpf_step(5, "devlink port GET command handled",
        textwrap.dedent("""
            kprobe:devlink_nl_port_get_doit {
                printf("DEVLINK_PORT_GET_DOIT pid=%d\\n", pid);
                exit();
            }
            interval:s:8 { exit(); }
        """),
        trigger="devlink port show 2>/dev/null; true",
        keyword="DEVLINK_PORT_GET_DOIT",
        timeout=10,
    )

def step6_devlink_params():
    print(f"\n── Step 6: devlink parameter listing works")
    r = run("devlink dev param show 2>/dev/null | head -5")
    if r and r.returncode == 0:
        print(f"{PASS}  devlink param show returned output")
        results.append((6, "devlink param show works", "PASS"))
    else:
        print(f"{SKIP}  devlink param show not available")
        results.append((6, "devlink param show works", "SKIP"))

def step7_devlink_health():
    print(f"\n── Step 7: devlink health reporters listed")
    r = run("devlink health show 2>/dev/null | head -5")
    if r and r.returncode == 0:
        out = r.stdout.strip()
        if out:
            print(f"{PASS}  Health reporters found")
            print(f"         {out[:200]}")
            results.append((7, "devlink health reporters", "PASS"))
        else:
            print(f"{SKIP}  No health reporters registered")
            results.append((7, "devlink health reporters", "SKIP"))
    else:
        print(f"{SKIP}  devlink health not available")
        results.append((7, "devlink health reporters", "SKIP"))

def step8_devlink_info():
    print(f"\n── Step 8: devlink info shows firmware versions")
    r = run("devlink dev info 2>/dev/null | head -10")
    if r and r.returncode == 0 and "fw" in r.stdout.lower():
        print(f"{PASS}  Firmware version info available")
        results.append((8, "devlink dev info fw version", "PASS"))
    elif r and r.returncode == 0:
        print(f"{SKIP}  devlink info works but no fw fields")
        results.append((8, "devlink dev info fw version", "SKIP"))
    else:
        print(f"{SKIP}  devlink info not available")
        results.append((8, "devlink dev info fw version", "SKIP"))

def step9_devlink_trap():
    print(f"\n── Step 9: devlink trap listing")
    r = run("devlink trap show 2>/dev/null | head -5")
    if r and r.returncode == 0 and r.stdout.strip():
        print(f"{PASS}  Packet traps registered")
        results.append((9, "devlink packet traps", "PASS"))
    else:
        print(f"{SKIP}  No devlink traps (no smart NIC?)")
        results.append((9, "devlink packet traps", "SKIP"))

def step10_devlink_genl():
    print(f"\n── Step 10: devlink Generic Netlink family registered")
    r = run("grep -c 'devlink' /proc/net/genl_ctrl 2>/dev/null || "
            "grep -c 'devlink' /sys/kernel/debug/tracing/available_events 2>/dev/null || "
            "grep -c ' devlink_genl' /proc/kallsyms")
    count = int(r.stdout.strip()) if r and r.returncode == 0 else 0
    if count > 0:
        print(f"{PASS}  devlink genl family present")
        results.append((10, "devlink genl family registered", "PASS"))
    else:
        print(f"{SKIP}  devlink genl not found")
        results.append((10, "devlink genl family registered", "SKIP"))

def print_summary():
    print("\n" + "═"*60)
    print("  devlink Subsystem Verification Summary")
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
    print("║      devlink Subsystem - Workflow Verification       ║")
    print("╚══════════════════════════════════════════════════════╝")
    check_prereqs()
    step1_symbols()
    step2_devlink_devices()
    step3_devlink_register()
    step4_devlink_nl_cmd_get()
    step5_devlink_port_get()
    step6_devlink_params()
    step7_devlink_health()
    step8_devlink_info()
    step9_devlink_trap()
    step10_devlink_genl()
    return print_summary()

if __name__ == "__main__":
    sys.exit(main())
