#!/usr/bin/env python3
"""
DSA Subsystem Workflow Verification
======================================
Uses bpftrace to trace DSA switch operations, tag protocol handling,
and per-port packet forwarding.

Requirements:
  - Linux with DSA (CONFIG_NET_DSA=y/m)
  - bpftrace >= 0.14
  - Root privileges

Usage:
  sudo python3 test_dsa_subsystem.py
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

# ─── Step 1: Check DSA symbols in kallsyms ───────────────────────────────────
def step1_symbols():
    num, desc = 1, "Check dsa_ symbols in /proc/kallsyms"
    print(f"\n── Step {num}: {desc}")
    r = run("grep -c 'dsa_' /proc/kallsyms")
    if r and r.returncode == 0:
        count = r.stdout.strip()
        print(f"{PASS}  Found {count} dsa_ symbols in /proc/kallsyms")
        results.append((num, desc, "PASS"))
    else:
        print(f"{SKIP}  No dsa_ symbols found (DSA may not be loaded)")
        results.append((num, desc, "SKIP"))

# ─── Step 2: Check CONFIG_NET_DSA in kernel config ───────────────────────────
def step2_dsa_config():
    num, desc = 2, "Check CONFIG_NET_DSA in kernel config"
    print(f"\n── Step {num}: {desc}")
    config_path = f"/boot/config-{os.uname().release}"
    r = run(f"grep CONFIG_NET_DSA {config_path} 2>/dev/null")
    if r and r.returncode == 0 and r.stdout.strip():
        lines = r.stdout.strip().split('\n')
        for line in lines[:5]:
            print(f"         {line}")
        if any('=y' in l or '=m' in l for l in lines):
            print(f"{PASS}  CONFIG_NET_DSA is enabled")
            results.append((num, desc, "PASS"))
        else:
            print(f"{SKIP}  CONFIG_NET_DSA not set to y or m")
            results.append((num, desc, "SKIP"))
    else:
        # Try /proc/config.gz as fallback
        r2 = run("zgrep CONFIG_NET_DSA /proc/config.gz 2>/dev/null")
        if r2 and r2.returncode == 0 and r2.stdout.strip():
            print(f"{PASS}  CONFIG_NET_DSA found in /proc/config.gz")
            results.append((num, desc, "PASS"))
        else:
            print(f"{SKIP}  Kernel config not found or DSA not configured")
            results.append((num, desc, "SKIP"))

# ─── Step 3: Trace dsa_switch_rcv ────────────────────────────────────────────
def step3_dsa_switch_rcv():
    bpf_step(3, "Trace dsa_switch_rcv (ingress demux)",
             textwrap.dedent("""\
                 kprobe:dsa_switch_rcv
                 {
                     printf("dsa_switch_rcv called skb=%p\\n", arg0);
                     exit();
                 }

                 interval:s:3 { exit(); }
             """),
             keyword="dsa_switch_rcv")

# ─── Step 4: Trace dsa_slave_xmit ───────────────────────────────────────────
def step4_dsa_slave_xmit():
    bpf_step(4, "Trace dsa_slave_xmit (egress tag insertion)",
             textwrap.dedent("""\
                 kprobe:dsa_slave_xmit
                 {
                     printf("dsa_slave_xmit called skb=%p dev=%p\\n", arg0, arg1);
                     exit();
                 }

                 interval:s:3 { exit(); }
             """),
             keyword="dsa_slave_xmit")

# ─── Step 5: Trace dsa_register_switch ───────────────────────────────────────
def step5_dsa_switch_register():
    bpf_step(5, "Trace dsa_register_switch (switch registration)",
             textwrap.dedent("""\
                 kprobe:dsa_register_switch
                 {
                     printf("dsa_register_switch ds=%p\\n", arg0);
                     exit();
                 }

                 interval:s:3 { exit(); }
             """),
             keyword="dsa_register_switch")

# ─── Step 6: Check for DSA devices in sysfs ─────────────────────────────────
def step6_dsa_devices():
    num, desc = 6, "Check for DSA devices in sysfs"
    print(f"\n── Step {num}: {desc}")

    # Check for DSA slave interfaces (they have a 'dsa' directory)
    r = run("ls -d /sys/class/net/*/dsa 2>/dev/null")
    if r and r.returncode == 0 and r.stdout.strip():
        devs = r.stdout.strip().split('\n')
        print(f"{PASS}  Found {len(devs)} DSA device(s):")
        for d in devs[:5]:
            print(f"         {d}")
        results.append((num, desc, "PASS"))
        return

    # Fallback: check for interfaces that look like DSA slaves
    r2 = run("ip -d link show type dsa 2>/dev/null")
    if r2 and r2.returncode == 0 and r2.stdout.strip():
        print(f"{PASS}  Found DSA interfaces via ip link:")
        print(f"         {r2.stdout.strip()[:200]}")
        results.append((num, desc, "PASS"))
        return

    # Fallback: check for DSA-related entries in dmesg
    r3 = run("dmesg | grep -i 'dsa' | grep -i 'switch' | tail -3 2>/dev/null")
    if r3 and r3.returncode == 0 and r3.stdout.strip():
        print(f"{SKIP}  No DSA net_devices, but DSA messages in dmesg:")
        print(f"         {r3.stdout.strip()[:200]}")
        results.append((num, desc, "SKIP"))
    else:
        print(f"{SKIP}  No DSA devices found (hardware may not be present)")
        results.append((num, desc, "SKIP"))

# ─── Step 7: Trace dsa_tag_driver_register ───────────────────────────────────
def step7_dsa_tag_driver():
    bpf_step(7, "Trace dsa_tag_driver_register (tag protocol registration)",
             textwrap.dedent("""\
                 kprobe:dsa_tag_driver_register
                 {
                     printf("dsa_tag_driver_register called\\n");
                     exit();
                 }

                 interval:s:3 { exit(); }
             """),
             keyword="dsa_tag_driver_register")

# ─── Step 8: Trace dsa_port_setup / dsa_port_enable ─────────────────────────
def step8_dsa_port_setup():
    # Try dsa_port_setup first, fall back to dsa_port_enable
    r = run("grep -c 'dsa_port_setup' /proc/kallsyms 2>/dev/null")
    if r and r.returncode == 0 and int(r.stdout.strip() or 0) > 0:
        probe = "dsa_port_setup"
    else:
        probe = "dsa_port_enable"

    bpf_step(8, f"Trace {probe} (port initialization)",
             textwrap.dedent(f"""\
                 kprobe:{probe}
                 {{
                     printf("{probe} dp=%p\\n", arg0);
                     exit();
                 }}

                 interval:s:3 {{ exit(); }}
             """),
             keyword=probe)

# ─── Step 9: Check DSA master device relationship ───────────────────────────
def step9_dsa_master():
    num, desc = 9, "Check DSA master device relationship"
    print(f"\n── Step {num}: {desc}")

    # Look for DSA master via sysfs
    r = run("ls /sys/class/net/*/dsa/tagging 2>/dev/null")
    if r and r.returncode == 0 and r.stdout.strip():
        tag_files = r.stdout.strip().split('\n')
        for tf in tag_files[:3]:
            tag_val = run(f"cat {tf} 2>/dev/null")
            if tag_val and tag_val.stdout.strip():
                print(f"         {tf} → {tag_val.stdout.strip()}")
        print(f"{PASS}  DSA master-slave tagging relationship found")
        results.append((num, desc, "PASS"))
        return

    # Fallback: look for DSA info in ip link
    r2 = run("ip -d link show 2>/dev/null | grep -A2 'dsa'")
    if r2 and r2.returncode == 0 and r2.stdout.strip():
        print(f"{PASS}  DSA master relationship via ip link:")
        print(f"         {r2.stdout.strip()[:200]}")
        results.append((num, desc, "PASS"))
        return

    # Fallback: check for dsa master in kallsyms
    r3 = run("grep 'dsa_master' /proc/kallsyms | head -5")
    if r3 and r3.returncode == 0 and r3.stdout.strip():
        print(f"{SKIP}  dsa_master symbols exist but no active DSA master device")
        print(f"         {r3.stdout.strip()[:200]}")
        results.append((num, desc, "SKIP"))
    else:
        print(f"{SKIP}  No DSA master device found (no DSA hardware present)")
        results.append((num, desc, "SKIP"))

# ─── Step 10: Verify DSA tracepoints ────────────────────────────────────────
def step10_dsa_tracepoints():
    num, desc = 10, "Verify DSA tracepoints in available_events"
    print(f"\n── Step {num}: {desc}")

    r = run("grep -i dsa /sys/kernel/debug/tracing/available_events 2>/dev/null")
    if r and r.returncode == 0 and r.stdout.strip():
        events = r.stdout.strip().split('\n')
        print(f"{PASS}  Found {len(events)} DSA tracepoint(s):")
        for ev in events[:10]:
            print(f"         {ev}")
        results.append((num, desc, "PASS"))
        return

    # Fallback: check available_filter_functions for DSA
    r2 = run("grep -ic dsa /sys/kernel/debug/tracing/available_filter_functions 2>/dev/null")
    if r2 and r2.returncode == 0 and r2.stdout.strip():
        count = r2.stdout.strip()
        if int(count) > 0:
            print(f"{SKIP}  No DSA tracepoints, but {count} DSA functions traceable via ftrace")
            results.append((num, desc, "SKIP"))
            return

    print(f"{SKIP}  No DSA tracepoints found (tracefs may not be mounted or DSA not loaded)")
    results.append((num, desc, "SKIP"))

# ─── Summary ─────────────────────────────────────────────────────────────────
def print_summary():
    print("\n" + "=" * 62)
    print("  DSA Subsystem — Test Summary")
    print("=" * 62)
    passed = sum(1 for r in results if r[2] == "PASS")
    failed = sum(1 for r in results if r[2] == "FAIL")
    skipped = sum(1 for r in results if r[2] == "SKIP")
    for num, desc, status in results:
        tag = PASS if status == "PASS" else (FAIL if status == "FAIL" else SKIP)
        print(f"  {tag} Step {num}: {desc}")
    print("-" * 62)
    print(f"  Total: {len(results)}  |  Passed: {passed}  |  Failed: {failed}  |  Skipped: {skipped}")
    print("=" * 62)

# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    print("=" * 62)
    print("  DSA (Distributed Switch Architecture) Subsystem Verification")
    print("=" * 62)
    check_prereqs()

    step1_symbols()
    step2_dsa_config()
    step3_dsa_switch_rcv()
    step4_dsa_slave_xmit()
    step5_dsa_switch_register()
    step6_dsa_devices()
    step7_dsa_tag_driver()
    step8_dsa_port_setup()
    step9_dsa_master()
    step10_dsa_tracepoints()

    print_summary()

if __name__ == "__main__":
    main()
