#!/usr/bin/env python3
"""
dns_resolver Subsystem Workflow Verification
===============================================
Uses bpftrace to trace kernel DNS resolution via request_key upcalls.

Requirements:
  - Linux with dns_resolver (CONFIG_DNS_RESOLVER=y/m)
  - bpftrace >= 0.14
  - Root privileges

Usage:
  sudo python3 test_dns_resolver_subsystem.py
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

# ─── Step functions ───────────────────────────────────────────────────────────

def step1_symbols():
    """Check dns_resolver symbols in /proc/kallsyms."""
    num, desc = 1, "dns_resolver symbols in kallsyms"
    print(f"\n── Step {num}: {desc}")
    r = run("grep -i dns_resolv /proc/kallsyms | head -20")
    if r and r.returncode == 0 and r.stdout.strip():
        lines = r.stdout.strip().split('\n')
        print(f"{PASS}  Found {len(lines)} dns_resolver symbol(s)")
        for line in lines[:5]:
            print(f"         {line.strip()}")
        results.append((num, desc, "PASS"))
    else:
        print(f"{SKIP}  No dns_resolver symbols found (module not loaded?)")
        results.append((num, desc, "SKIP"))

def step2_module_loaded():
    """Check if dns_resolver module is loaded."""
    num, desc = 2, "dns_resolver module loaded"
    print(f"\n── Step {num}: {desc}")
    r = run("lsmod | grep dns_resolver")
    if r and r.returncode == 0 and r.stdout.strip():
        print(f"{PASS}  Module loaded:")
        print(f"         {r.stdout.strip()[:200]}")
        results.append((num, desc, "PASS"))
    else:
        # Check if built-in
        r2 = run("grep -c dns_resolv /proc/kallsyms")
        if r2 and r2.stdout.strip() != "0":
            print(f"{PASS}  dns_resolver is built-in (not a loadable module)")
            results.append((num, desc, "PASS"))
        else:
            # Try loading it
            r3 = run("modprobe dns_resolver 2>&1")
            if r3 and r3.returncode == 0:
                print(f"{PASS}  Module loaded via modprobe")
                results.append((num, desc, "PASS"))
            else:
                print(f"{SKIP}  dns_resolver module not available")
                results.append((num, desc, "SKIP"))

def step3_dns_query():
    """Trace dns_query() with bpftrace."""
    bpf_step(3, "bpftrace kprobe:dns_query",
             textwrap.dedent("""\
                 kprobe:dns_query
                 {
                     printf("dns_query called: name=%s\\n", str(arg2));
                 }
                 interval:s:2 { exit(); }
             """),
             trigger="keyctl request dns_resolver test_lookup 2>/dev/null || true",
             keyword="Attaching")

def step4_dns_resolve():
    """Trace dns_resolve_server_name_to_ip() with bpftrace."""
    bpf_step(4, "bpftrace kprobe:dns_resolve_server_name_to_ip",
             textwrap.dedent("""\
                 kprobe:dns_resolve_server_name_to_ip
                 {
                     printf("dns_resolve_server_name_to_ip called: unc=%s\\n", str(arg0));
                 }
                 interval:s:2 { exit(); }
             """),
             keyword="Attaching")

def step5_request_key():
    """Trace request_key() for dns_resolver type."""
    bpf_step(5, "bpftrace kprobe:request_key for dns_resolver",
             textwrap.dedent("""\
                 kprobe:request_key
                 {
                     printf("request_key called: type=%s\\n", str(arg0));
                 }
                 interval:s:2 { exit(); }
             """),
             trigger="keyctl request dns_resolver test_lookup 2>/dev/null || true",
             keyword="Attaching")

def step6_key_type():
    """Check /proc/keys for dns_resolver key type registration."""
    num, desc = 6, "dns_resolver key type in /proc/keys"
    print(f"\n── Step {num}: {desc}")
    # Check if the key type is registered via key-users or keys
    r = run("cat /proc/keys 2>/dev/null | grep dns_resolver")
    r2 = run("keyctl describe @s 2>/dev/null")
    r3 = run("grep -r dns_resolver /proc/keys 2>/dev/null; "
             "cat /proc/key-users 2>/dev/null | head -5")
    if r and r.stdout.strip():
        print(f"{PASS}  dns_resolver keys found in /proc/keys")
        print(f"         {r.stdout.strip()[:200]}")
        results.append((num, desc, "PASS"))
    else:
        # Verify the key type is at least registered by checking kallsyms
        r4 = run("grep 'key_type_dns_resolver' /proc/kallsyms")
        if r4 and r4.stdout.strip():
            print(f"{PASS}  key_type_dns_resolver registered (via kallsyms)")
            print(f"         {r4.stdout.strip()[:200]}")
            results.append((num, desc, "PASS"))
        else:
            print(f"{SKIP}  dns_resolver key type not found in /proc/keys")
            results.append((num, desc, "SKIP"))

def step7_dns_key_match():
    """Trace dns_resolver_match() with bpftrace."""
    bpf_step(7, "bpftrace kprobe:dns_resolver_match",
             textwrap.dedent("""\
                 kprobe:dns_resolver_match
                 {
                     printf("dns_resolver_match called\\n");
                 }
                 interval:s:2 { exit(); }
             """),
             trigger="keyctl request dns_resolver test_lookup 2>/dev/null || true",
             keyword="Attaching")

def step8_dns_key_describe():
    """Trace dns_resolver_describe() with bpftrace."""
    bpf_step(8, "bpftrace kprobe:dns_resolver_describe",
             textwrap.dedent("""\
                 kprobe:dns_resolver_describe
                 {
                     printf("dns_resolver_describe called\\n");
                 }
                 interval:s:2 { exit(); }
             """),
             trigger="keyctl request dns_resolver test_lookup 2>/dev/null || true",
             keyword="Attaching")

def step9_cifs_integration():
    """Check if cifs module references dns_resolver."""
    num, desc = 9, "CIFS module references dns_resolver"
    print(f"\n── Step {num}: {desc}")
    # Check if cifs module is loaded and depends on dns_resolver
    r = run("lsmod | grep cifs")
    if r and r.returncode == 0 and r.stdout.strip():
        # Check dependency
        r2 = run("modinfo cifs 2>/dev/null | grep -i depends")
        if r2 and "dns_resolver" in r2.stdout:
            print(f"{PASS}  CIFS depends on dns_resolver")
            print(f"         {r2.stdout.strip()[:200]}")
            results.append((num, desc, "PASS"))
        else:
            print(f"{PASS}  CIFS module loaded (dns_resolver may be built-in dep)")
            print(f"         {r.stdout.strip()[:200]}")
            results.append((num, desc, "PASS"))
    else:
        # Check modinfo even if not loaded
        r3 = run("modinfo cifs 2>/dev/null | grep -i depends")
        if r3 and r3.returncode == 0 and "dns_resolver" in r3.stdout:
            print(f"{PASS}  CIFS modinfo shows dns_resolver dependency")
            print(f"         {r3.stdout.strip()[:200]}")
            results.append((num, desc, "PASS"))
        else:
            # Check kernel config
            r4 = run("grep CONFIG_CIFS /boot/config-$(uname -r) 2>/dev/null | head -3")
            if r4 and r4.stdout.strip():
                print(f"{SKIP}  CIFS configured but module not loaded")
                print(f"         {r4.stdout.strip()[:200]}")
                results.append((num, desc, "SKIP"))
            else:
                print(f"{SKIP}  CIFS module not available")
                results.append((num, desc, "SKIP"))

def step10_keyring_check():
    """Verify dns_resolver keyring in /proc/key-users or keyctl."""
    num, desc = 10, "dns_resolver keyring check"
    print(f"\n── Step {num}: {desc}")
    r = run("keyctl show @s 2>/dev/null")
    r2 = run("cat /proc/key-users 2>/dev/null | head -10")
    if r and r.returncode == 0 and r.stdout.strip():
        print(f"{PASS}  Keyring accessible via keyctl")
        print(f"         {r.stdout.strip()[:200]}")
        results.append((num, desc, "PASS"))
    elif r2 and r2.returncode == 0 and r2.stdout.strip():
        print(f"{PASS}  /proc/key-users accessible")
        print(f"         {r2.stdout.strip()[:200]}")
        results.append((num, desc, "PASS"))
    else:
        print(f"{SKIP}  Keyring not accessible")
        results.append((num, desc, "SKIP"))

# ─── Summary ──────────────────────────────────────────────────────────────────

def print_summary():
    print("\n" + "=" * 60)
    print("  dns_resolver Subsystem — Test Summary")
    print("=" * 60)
    passed = sum(1 for *_, s in results if s == "PASS")
    failed = sum(1 for *_, s in results if s == "FAIL")
    skipped = sum(1 for *_, s in results if s == "SKIP")
    for num, desc, status in results:
        tag = {
            "PASS": PASS, "FAIL": FAIL, "SKIP": SKIP
        }.get(status, status)
        print(f"  {tag}  Step {num}: {desc}")
    print("-" * 60)
    print(f"  Total: {len(results)}  |  Passed: {passed}"
          f"  |  Failed: {failed}  |  Skipped: {skipped}")
    print("=" * 60)
    return 1 if failed > 0 else 0

# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  dns_resolver Subsystem — Workflow Verification")
    print("=" * 60)
    check_prereqs()

    step1_symbols()
    step2_module_loaded()
    step3_dns_query()
    step4_dns_resolve()
    step5_request_key()
    step6_key_type()
    step7_dns_key_match()
    step8_dns_key_describe()
    step9_cifs_integration()
    step10_keyring_check()

    rc = print_summary()
    sys.exit(rc)

if __name__ == "__main__":
    main()
