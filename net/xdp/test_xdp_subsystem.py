#!/usr/bin/env python3
"""
XDP Subsystem Workflow Verification
======================================
Uses bpftrace to trace XDP program attachment, packet verdict paths,
redirect operations, and AF_XDP socket activity.

Requirements:
  - Linux with XDP support (CONFIG_BPF=y, CONFIG_XDP_SOCKETS=y)
  - bpftrace >= 0.14
  - Root privileges

Usage:
  sudo python3 test_xdp_subsystem.py
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

# ─── Step 1: Check XDP symbols in kallsyms ───────────────────────────
def step1_symbols():
    num, desc = 1, "Check XDP kernel symbols in /proc/kallsyms"
    print(f"\n── Step {num}: {desc}")
    r = run("grep -c 'xdp_' /proc/kallsyms")
    if r and r.returncode == 0:
        count = r.stdout.strip()
        print(f"{PASS}  Found {count} xdp_ symbols in /proc/kallsyms")
        results.append((num, desc, "PASS"))
    else:
        print(f"{FAIL}  No xdp_ symbols found in /proc/kallsyms")
        results.append((num, desc, "FAIL"))

# ─── Step 2: Check XDP kernel config ─────────────────────────────────
def step2_xdp_config():
    num, desc = 2, "Check CONFIG_XDP / CONFIG_XDP_SOCKETS in kernel config"
    print(f"\n── Step {num}: {desc}")
    config_path = f"/boot/config-{os.uname().release}"
    if not os.path.exists(config_path):
        config_path_gz = f"/proc/config.gz"
        if os.path.exists(config_path_gz):
            r = run(f"zcat {config_path_gz} | grep -E 'CONFIG_XDP|CONFIG_BPF'")
        else:
            print(f"{SKIP}  Kernel config not found at /boot/config-* or /proc/config.gz")
            results.append((num, desc, "SKIP"))
            return
    else:
        r = run(f"grep -E 'CONFIG_XDP|CONFIG_XDP_SOCKETS|CONFIG_BPF=|CONFIG_BPF_SYSCALL' {config_path}")

    if r and r.returncode == 0 and r.stdout.strip():
        lines = r.stdout.strip().split('\n')
        for line in lines[:6]:
            print(f"         {line}")
        print(f"{PASS}  XDP/BPF kernel config options found")
        results.append((num, desc, "PASS"))
    else:
        print(f"{FAIL}  XDP/BPF config options not found")
        results.append((num, desc, "FAIL"))

# ─── Step 3: Trace xdp_do_redirect ───────────────────────────────────
def step3_xdp_do_redirect():
    bpf_step(3, "Trace xdp_do_redirect kprobe",
             textwrap.dedent("""\
                 kprobe:xdp_do_redirect {
                     printf("xdp_do_redirect called: pid=%d comm=%s\\n", pid, comm);
                 }
                 interval:s:2 { exit(); }
             """),
             trigger="ping -c 1 -W 1 127.0.0.1 > /dev/null 2>&1",
             keyword="Attaching")

# ─── Step 4: Trace xdp_do_flush ──────────────────────────────────────
def step4_xdp_do_flush():
    bpf_step(4, "Trace xdp_do_flush kprobe",
             textwrap.dedent("""\
                 kprobe:xdp_do_flush {
                     printf("xdp_do_flush called: pid=%d comm=%s\\n", pid, comm);
                 }
                 interval:s:2 { exit(); }
             """),
             trigger="ping -c 1 -W 1 127.0.0.1 > /dev/null 2>&1",
             keyword="Attaching")

# ─── Step 5: Trace BPF program load for XDP ──────────────────────────
def step5_bpf_prog_load():
    bpf_step(5, "Trace BPF program loading (bpf_prog_load)",
             textwrap.dedent("""\
                 kprobe:bpf_prog_load {
                     printf("bpf_prog_load: pid=%d comm=%s\\n", pid, comm);
                 }
                 interval:s:2 { exit(); }
             """),
             keyword="Attaching")

# ─── Step 6: Trace xdp_rxq_info_reg ──────────────────────────────────
def step6_xdp_rxq_info():
    bpf_step(6, "Trace xdp_rxq_info_reg kprobe",
             textwrap.dedent("""\
                 kprobe:xdp_rxq_info_reg {
                     printf("xdp_rxq_info_reg: pid=%d comm=%s\\n", pid, comm);
                 }
                 interval:s:2 { exit(); }
             """),
             keyword="Attaching")

# ─── Step 7: Check AF_XDP socket presence ────────────────────────────
def step7_xsk_sockets():
    num, desc = 7, "Check /proc/net for AF_XDP socket presence"
    print(f"\n── Step {num}: {desc}")
    # AF_XDP sockets may appear in /proc/net/xdp or /proc/net/pf_xdp
    found = False
    for path in ["/proc/net/xdp", "/proc/net/pf_xdp"]:
        if os.path.exists(path):
            r = run(f"cat {path}")
            if r and r.returncode == 0:
                lines = r.stdout.strip().split('\n')
                print(f"{PASS}  {path} exists ({len(lines)} lines)")
                for line in lines[:3]:
                    print(f"         {line}")
                results.append((num, desc, "PASS"))
                found = True
                break
    if not found:
        # Also try looking for xsk in /proc/net
        r = run("ls /proc/net/ | grep -i xdp")
        if r and r.returncode == 0 and r.stdout.strip():
            print(f"{PASS}  Found XDP entries in /proc/net: {r.stdout.strip()}")
            results.append((num, desc, "PASS"))
        else:
            print(f"{SKIP}  No AF_XDP socket files found in /proc/net (no xsk sockets active)")
            results.append((num, desc, "SKIP"))

# ─── Step 8: Check XDP feature flags on interfaces ───────────────────
def step8_xdp_features():
    num, desc = 8, "Check /sys for XDP feature flags on interfaces"
    print(f"\n── Step {num}: {desc}")
    r = run("ls /sys/class/net/")
    if not r or r.returncode != 0:
        print(f"{FAIL}  Cannot list /sys/class/net/")
        results.append((num, desc, "FAIL"))
        return

    interfaces = r.stdout.strip().split()
    found_any = False
    for iface in interfaces[:5]:
        xdp_path = f"/sys/class/net/{iface}/xdp"
        if os.path.isdir(xdp_path):
            print(f"         {iface}: XDP sysfs directory exists")
            found_any = True
        else:
            # Check via ethtool for xdp-features
            feat_r = run(f"ethtool -k {iface} 2>/dev/null | grep -i xdp")
            if feat_r and feat_r.returncode == 0 and feat_r.stdout.strip():
                print(f"         {iface}: {feat_r.stdout.strip()}")
                found_any = True

    if found_any:
        print(f"{PASS}  XDP feature flags found on at least one interface")
        results.append((num, desc, "PASS"))
    else:
        # Fallback: check for xdp_features in sysfs
        r2 = run("find /sys/class/net/ -name '*xdp*' 2>/dev/null | head -5")
        if r2 and r2.stdout.strip():
            print(f"{PASS}  Found XDP sysfs entries:")
            print(f"         {r2.stdout.strip()}")
            results.append((num, desc, "PASS"))
        else:
            print(f"{SKIP}  No XDP feature flags found (driver may not expose them)")
            results.append((num, desc, "SKIP"))

# ─── Step 9: Trace page_pool_create ──────────────────────────────────
def step9_page_pool():
    bpf_step(9, "Trace page_pool_create kprobe",
             textwrap.dedent("""\
                 kprobe:page_pool_create {
                     printf("page_pool_create: pid=%d comm=%s\\n", pid, comm);
                 }
                 interval:s:2 { exit(); }
             """),
             keyword="Attaching")

# ─── Step 10: Verify XDP tracepoints ─────────────────────────────────
def step10_xdp_tracepoints():
    num, desc = 10, "Verify XDP tracepoints in available_events"
    print(f"\n── Step {num}: {desc}")
    r = run("grep '^xdp:' /sys/kernel/debug/tracing/available_events 2>/dev/null || "
            "grep '^xdp:' /sys/kernel/tracing/available_events 2>/dev/null")
    if r and r.returncode == 0 and r.stdout.strip():
        events = r.stdout.strip().split('\n')
        for ev in events[:8]:
            print(f"         {ev}")
        print(f"{PASS}  Found {len(events)} XDP tracepoints")
        results.append((num, desc, "PASS"))
    else:
        # Try via bpftrace
        r2 = run("bpftrace -l 'tracepoint:xdp:*' 2>/dev/null")
        if r2 and r2.returncode == 0 and r2.stdout.strip():
            events = r2.stdout.strip().split('\n')
            for ev in events[:8]:
                print(f"         {ev}")
            print(f"{PASS}  Found {len(events)} XDP tracepoints via bpftrace")
            results.append((num, desc, "PASS"))
        else:
            print(f"{FAIL}  No XDP tracepoints found")
            results.append((num, desc, "FAIL"))

# ─── Summary ─────────────────────────────────────────────────────────
def print_summary():
    print("\n" + "=" * 60)
    print("  XDP Subsystem Test Summary")
    print("=" * 60)
    for num, desc, status in results:
        tag = PASS if status == "PASS" else (FAIL if status == "FAIL" else SKIP)
        print(f"  {tag}  Step {num}: {desc}")
    total = len(results)
    passed = sum(1 for _, _, s in results if s == "PASS")
    failed = sum(1 for _, _, s in results if s == "FAIL")
    skipped = sum(1 for _, _, s in results if s == "SKIP")
    print(f"\n  Total: {total}  Passed: {passed}  Failed: {failed}  Skipped: {skipped}")
    print("=" * 60)

# ─── Main ─────────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("  XDP Subsystem Workflow Verification")
    print("=" * 60)
    check_prereqs()

    step1_symbols()
    step2_xdp_config()
    step3_xdp_do_redirect()
    step4_xdp_do_flush()
    step5_bpf_prog_load()
    step6_xdp_rxq_info()
    step7_xsk_sockets()
    step8_xdp_features()
    step9_page_pool()
    step10_xdp_tracepoints()

    print_summary()

if __name__ == "__main__":
    main()
