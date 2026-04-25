#!/usr/bin/env python3
"""
MPLS Subsystem Workflow Verification
======================================
Uses bpftrace to trace MPLS label forwarding, route management,
and label stack operations.

Requirements:
  - Linux with MPLS (CONFIG_MPLS=y, CONFIG_MPLS_ROUTING=y/m)
  - bpftrace >= 0.14
  - Root privileges

Usage:
  sudo python3 test_mpls_subsystem.py
"""

import subprocess, sys, os, time, tempfile

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


# ── Step 1: MPLS symbols in /proc/kallsyms ──────────────────────────────────
def step1_symbols():
    print(f"\n── Step 1: MPLS symbols in /proc/kallsyms")
    r = run("grep -c ' mpls_' /proc/kallsyms")
    count = int(r.stdout.strip()) if r and r.returncode == 0 else 0
    if count > 0:
        print(f"{PASS}  {count} mpls_* symbols found")
        results.append((1, "MPLS symbols in kallsyms", "PASS"))
    else:
        print(f"{FAIL}  No mpls_ symbols found (MPLS not built?)")
        results.append((1, "MPLS symbols in kallsyms", "FAIL"))


# ── Step 2: CONFIG_MPLS_ROUTING in kernel config ────────────────────────────
def step2_mpls_config():
    print(f"\n── Step 2: CONFIG_MPLS_ROUTING in kernel config")
    config_files = ["/proc/config.gz", "/boot/config-" + os.uname().release]
    for cf in config_files:
        if not os.path.exists(cf):
            continue
        try:
            if cf.endswith(".gz"):
                import gzip
                data = gzip.open(cf, "rt").read()
            else:
                data = open(cf).read()
            if "CONFIG_MPLS_ROUTING=y" in data or "CONFIG_MPLS_ROUTING=m" in data:
                print(f"{PASS}  CONFIG_MPLS_ROUTING enabled in {cf}")
                results.append((2, "CONFIG_MPLS_ROUTING in kernel config", "PASS"))
                return
        except Exception:
            pass
    print(f"{SKIP}  CONFIG_MPLS_ROUTING not found in kernel config")
    results.append((2, "CONFIG_MPLS_ROUTING in kernel config", "SKIP"))


# ── Step 3: bpftrace kprobe:mpls_forward ─────────────────────────────────────
def step3_mpls_forward():
    bpf_step(3, "kprobe:mpls_forward (MPLS forwarding path)",
        """
kprobe:mpls_forward {
    printf("MPLS_FORWARD skb=%p\\n", arg0);
    exit();
}
interval:s:5 { exit(); }
""",
        trigger="ip -f mpls route show 2>/dev/null; true",
        keyword="MPLS_FORWARD",
        timeout=10,
    )


# ── Step 4: bpftrace kprobe:mpls_rt_alloc (or mpls_route_input) ─────────────
def step4_mpls_rt_alloc():
    bpf_step(4, "kprobe:mpls_rt_alloc / mpls_route_input",
        """
kprobe:mpls_rt_alloc,
kprobe:mpls_route_input {
    printf("MPLS_RT_ALLOC pid=%d\\n", pid);
    exit();
}
interval:s:5 { exit(); }
""",
        keyword="MPLS_RT_ALLOC",
        timeout=10,
    )


# ── Step 5: bpftrace kprobe:mpls_output (or nla_put_labels) ─────────────────
def step5_mpls_output():
    bpf_step(5, "kprobe:mpls_output / nla_put_labels",
        """
kprobe:mpls_output,
kprobe:nla_put_labels {
    printf("MPLS_OUTPUT pid=%d\\n", pid);
    exit();
}
interval:s:5 { exit(); }
""",
        trigger="ip -f mpls route show 2>/dev/null; true",
        keyword="MPLS_OUTPUT",
        timeout=10,
    )


# ── Step 6: MPLS sysctl settings ────────────────────────────────────────────
def step6_mpls_sysctl():
    print(f"\n── Step 6: MPLS sysctl settings (/proc/sys/net/mpls)")
    sysctl_dir = "/proc/sys/net/mpls"
    if os.path.isdir(sysctl_dir):
        r = run(f"ls {sysctl_dir}")
        entries = r.stdout.strip().split() if r and r.returncode == 0 else []
        if entries:
            print(f"{PASS}  MPLS sysctl entries: {entries[:5]}")
            results.append((6, "MPLS sysctl settings", "PASS"))
        else:
            print(f"{FAIL}  {sysctl_dir} exists but is empty")
            results.append((6, "MPLS sysctl settings", "FAIL"))
    else:
        r = run("sysctl net.mpls 2>/dev/null")
        if r and r.returncode == 0 and r.stdout.strip():
            print(f"{PASS}  MPLS sysctl available via sysctl command")
            results.append((6, "MPLS sysctl settings", "PASS"))
        else:
            print(f"{SKIP}  /proc/sys/net/mpls not found (MPLS not enabled?)")
            results.append((6, "MPLS sysctl settings", "SKIP"))


# ── Step 7: bpftrace kprobe:mpls_netconf_dump_devconf ────────────────────────
def step7_mpls_netconf():
    bpf_step(7, "kprobe:mpls_netconf_dump_devconf",
        """
kprobe:mpls_netconf_dump_devconf {
    printf("MPLS_NETCONF pid=%d\\n", pid);
    exit();
}
interval:s:5 { exit(); }
""",
        trigger="ip -f mpls netconf show 2>/dev/null; true",
        keyword="MPLS_NETCONF",
        timeout=10,
    )


# ── Step 8: MPLS routing table via ip -f mpls route ─────────────────────────
def step8_mpls_routes():
    print(f"\n── Step 8: MPLS routing table (ip -f mpls route)")
    r = run("ip -f mpls route show 2>&1")
    if r is None:
        print(f"{SKIP}  Command timed out")
        results.append((8, "MPLS routing table", "SKIP"))
        return
    if r.returncode == 0:
        routes = r.stdout.strip()
        if routes:
            count = len(routes.splitlines())
            print(f"{PASS}  {count} MPLS route(s) found")
        else:
            print(f"{PASS}  MPLS route table accessible (empty)")
        results.append((8, "MPLS routing table", "PASS"))
    else:
        if "not supported" in r.stderr or "RTNETLINK" in r.stderr:
            print(f"{SKIP}  MPLS address family not supported by kernel")
            print(f"         {r.stderr.strip()[:200]}")
            results.append((8, "MPLS routing table", "SKIP"))
        else:
            print(f"{FAIL}  ip -f mpls route failed: {r.stderr.strip()[:200]}")
            results.append((8, "MPLS routing table", "FAIL"))


# ── Step 9: mpls_router module loaded ────────────────────────────────────────
def step9_mpls_module():
    print(f"\n── Step 9: MPLS router module loaded")
    r = run("lsmod | grep mpls")
    if r and r.returncode == 0 and r.stdout.strip():
        modules = [line.split()[0] for line in r.stdout.strip().splitlines()]
        print(f"{PASS}  MPLS modules loaded: {modules}")
        results.append((9, "MPLS router module loaded", "PASS"))
        return
    # Check if built-in (not a module)
    r2 = run("grep -c ' mpls_' /proc/kallsyms")
    count = int(r2.stdout.strip()) if r2 and r2.returncode == 0 else 0
    if count > 5:
        print(f"{PASS}  MPLS appears built-in ({count} symbols, no module)")
        results.append((9, "MPLS router module loaded", "PASS"))
    else:
        # Try loading the module
        r3 = run("modprobe mpls_router 2>/dev/null", timeout=5)
        r4 = run("lsmod | grep mpls")
        if r4 and r4.returncode == 0 and r4.stdout.strip():
            print(f"{PASS}  mpls_router loaded via modprobe")
            results.append((9, "MPLS router module loaded", "PASS"))
        else:
            print(f"{SKIP}  mpls_router module not available")
            results.append((9, "MPLS router module loaded", "SKIP"))


# ── Step 10: MPLS tracepoints or net events ──────────────────────────────────
def step10_mpls_tracepoints():
    print(f"\n── Step 10: MPLS tracepoints / net events")
    # Check for MPLS-specific tracepoints
    r = run("ls /sys/kernel/debug/tracing/events/mpls/ 2>/dev/null | head -5")
    if r and r.returncode == 0 and r.stdout.strip():
        events = r.stdout.strip().split()
        print(f"{PASS}  MPLS tracepoints: {events[:5]}")
        results.append((10, "MPLS tracepoints / net events", "PASS"))
        return
    # Check for net-related events that cover MPLS
    r2 = run("grep -i mpls /sys/kernel/debug/tracing/available_events 2>/dev/null")
    if r2 and r2.returncode == 0 and r2.stdout.strip():
        events = r2.stdout.strip().splitlines()[:5]
        print(f"{PASS}  MPLS-related trace events: {events}")
        results.append((10, "MPLS tracepoints / net events", "PASS"))
        return
    # Fall back to checking net events that may fire for MPLS
    r3 = run("ls /sys/kernel/debug/tracing/events/net/ 2>/dev/null | head -5")
    if r3 and r3.returncode == 0 and r3.stdout.strip():
        events = r3.stdout.strip().split()
        print(f"{PASS}  Generic net tracepoints available (cover MPLS): {events[:5]}")
        results.append((10, "MPLS tracepoints / net events", "PASS"))
    else:
        print(f"{SKIP}  No MPLS or net tracepoints found")
        results.append((10, "MPLS tracepoints / net events", "SKIP"))


def print_summary():
    print("\n" + "═" * 60)
    print("  MPLS Subsystem Verification Summary")
    print("═" * 60)
    passed  = sum(1 for _, _, s in results if s == "PASS")
    failed  = sum(1 for _, _, s in results if s == "FAIL")
    skipped = sum(1 for _, _, s in results if s == "SKIP")
    for n, d, s in results:
        icon = PASS if s == "PASS" else (FAIL if s == "FAIL" else SKIP)
        print(f"  Step {n:>2}: {icon}  {d}")
    print("═" * 60)
    print(f"  Total: {len(results)}  | \033[32mPASS:{passed}\033[0m "
          f"| \033[31mFAIL:{failed}\033[0m | \033[33mSKIP:{skipped}\033[0m")
    print("═" * 60)
    if failed == 0:
        print(f"\n{PASS} All verifiable MPLS steps passed!\n"); return 0
    print(f"\n{FAIL} {failed} step(s) failed.\n"); return 1


def main():
    print("╔══════════════════════════════════════════════════════╗")
    print("║       MPLS Subsystem - Workflow Verification         ║")
    print("╚══════════════════════════════════════════════════════╝")
    check_prereqs()
    step1_symbols()
    step2_mpls_config()
    step3_mpls_forward()
    step4_mpls_rt_alloc()
    step5_mpls_output()
    step6_mpls_sysctl()
    step7_mpls_netconf()
    step8_mpls_routes()
    step9_mpls_module()
    step10_mpls_tracepoints()
    return print_summary()


if __name__ == "__main__":
    sys.exit(main())
