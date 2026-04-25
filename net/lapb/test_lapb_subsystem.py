#!/usr/bin/env python3
"""
LAPB Subsystem Workflow Verification
======================================
Uses bpftrace to trace LAPB frame processing, state machine
transitions, and X.25 data link operations.

Requirements:
  - Linux with LAPB (CONFIG_LAPB=y/m)
  - bpftrace >= 0.14
  - Root privileges

Usage:
  sudo python3 test_lapb_subsystem.py
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


# ── Step 1: LAPB symbols in kallsyms ────────────────────────────────────────
def step1_symbols():
    print(f"\n── Step 1: LAPB symbols in kernel")
    r = run("grep -c ' lapb_' /proc/kallsyms")
    count = int(r.stdout.strip()) if r and r.returncode == 0 else 0
    if count > 0:
        print(f"{PASS}  {count} lapb_* symbols found")
        results.append((1, "LAPB symbols in kallsyms", "PASS"))
    else:
        print(f"{FAIL}  No lapb_ symbols found (CONFIG_LAPB not enabled?)")
        results.append((1, "LAPB symbols in kallsyms", "FAIL"))


# ── Step 2: LAPB module loaded ──────────────────────────────────────────────
def step2_module_loaded():
    print(f"\n── Step 2: LAPB module loaded")
    r = run("lsmod | grep -i lapb")
    if r and r.returncode == 0 and r.stdout.strip():
        print(f"{PASS}  lapb module is loaded")
        print(f"         {r.stdout.strip()[:200]}")
        results.append((2, "LAPB module loaded", "PASS"))
    else:
        # Check if built-in
        r2 = run("grep ' lapb_' /proc/kallsyms | head -1")
        if r2 and r2.returncode == 0 and r2.stdout.strip():
            print(f"{PASS}  lapb appears built-in (symbols present, no module)")
            results.append((2, "LAPB module loaded", "PASS"))
        else:
            print(f"{SKIP}  lapb module not loaded (try: modprobe lapb)")
            results.append((2, "LAPB module loaded", "SKIP"))


# ── Step 3: lapb_data_received ──────────────────────────────────────────────
def step3_lapb_data_received():
    bpf_step(3, "lapb_data_received — incoming frame processing",
        """
        kprobe:lapb_data_received {
            printf("LAPB_DATA_RECEIVED dev=%p skb=%p\\n", arg0, arg1);
            exit();
        }
        interval:s:5 { exit(); }
        """,
        keyword="LAPB_DATA_RECEIVED",
        timeout=8,
    )


# ── Step 4: lapb_data_request ───────────────────────────────────────────────
def step4_lapb_data_request():
    bpf_step(4, "lapb_data_request — outgoing data frame",
        """
        kprobe:lapb_data_request {
            printf("LAPB_DATA_REQUEST dev=%p skb=%p\\n", arg0, arg1);
            exit();
        }
        interval:s:5 { exit(); }
        """,
        keyword="LAPB_DATA_REQUEST",
        timeout=8,
    )


# ── Step 5: lapb_connect_request ────────────────────────────────────────────
def step5_lapb_connect():
    bpf_step(5, "lapb_connect_request — link setup (SABM)",
        """
        kprobe:lapb_connect_request {
            printf("LAPB_CONNECT dev=%p\\n", arg0);
            exit();
        }
        interval:s:5 { exit(); }
        """,
        keyword="LAPB_CONNECT",
        timeout=8,
    )


# ── Step 6: lapb_disconnect_request ─────────────────────────────────────────
def step6_lapb_disconnect():
    bpf_step(6, "lapb_disconnect_request — link teardown (DISC)",
        """
        kprobe:lapb_disconnect_request {
            printf("LAPB_DISCONNECT dev=%p\\n", arg0);
            exit();
        }
        interval:s:5 { exit(); }
        """,
        keyword="LAPB_DISCONNECT",
        timeout=8,
    )


# ── Step 7: lapb_state machine ──────────────────────────────────────────────
def step7_lapb_state_machine():
    bpf_step(7, "lapb_state1_machine / lapb_state2_machine — state handlers",
        """
        kprobe:lapb_state1_machine {
            printf("LAPB_STATE_MACHINE state1 cb=%p\\n", arg0);
            exit();
        }
        kprobe:lapb_state2_machine {
            printf("LAPB_STATE_MACHINE state2 cb=%p\\n", arg0);
            exit();
        }
        interval:s:5 { exit(); }
        """,
        keyword="LAPB_STATE_MACHINE",
        timeout=8,
    )


# ── Step 8: lapb_validate_nr ────────────────────────────────────────────────
def step8_lapb_validate():
    bpf_step(8, "lapb_validate_nr — sequence number validation",
        """
        kprobe:lapb_validate_nr {
            printf("LAPB_VALIDATE_NR cb=%p nr=%d\\n", arg0, arg1);
            exit();
        }
        interval:s:5 { exit(); }
        """,
        keyword="LAPB_VALIDATE_NR",
        timeout=8,
    )


# ── Step 9: lapb_timer ──────────────────────────────────────────────────────
def step9_lapb_timer():
    bpf_step(9, "lapb_t1timer_expiry / lapb_t2timer_expiry — retransmit timers",
        """
        kprobe:lapb_t1timer_expiry {
            printf("LAPB_TIMER t1_expiry timer=%p\\n", arg0);
            exit();
        }
        kprobe:lapb_t2timer_expiry {
            printf("LAPB_TIMER t2_expiry timer=%p\\n", arg0);
            exit();
        }
        interval:s:5 { exit(); }
        """,
        keyword="LAPB_TIMER",
        timeout=8,
    )


# ── Step 10: CONFIG_LAPB in kernel config ───────────────────────────────────
def step10_lapb_config():
    print(f"\n── Step 10: CONFIG_LAPB in kernel config")
    configured = False
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
            if "CONFIG_LAPB=y" in data or "CONFIG_LAPB=m" in data:
                configured = True
                break
        except Exception:
            pass
    if configured:
        print(f"{PASS}  CONFIG_LAPB is enabled")
        results.append((10, "CONFIG_LAPB in kernel config", "PASS"))
    else:
        print(f"{SKIP}  CONFIG_LAPB not found in kernel config")
        results.append((10, "CONFIG_LAPB in kernel config", "SKIP"))


def print_summary():
    print("\n" + "═" * 60)
    print("  LAPB Subsystem Verification Summary")
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
        print(f"\n{PASS} All verifiable steps passed!\n"); return 0
    print(f"\n{FAIL} {failed} step(s) failed.\n"); return 1


def main():
    print("╔══════════════════════════════════════════════════════╗")
    print("║        LAPB Subsystem - Workflow Verification        ║")
    print("╚══════════════════════════════════════════════════════╝")
    check_prereqs()
    step1_symbols()
    step2_module_loaded()
    step3_lapb_data_received()
    step4_lapb_data_request()
    step5_lapb_connect()
    step6_lapb_disconnect()
    step7_lapb_state_machine()
    step8_lapb_validate()
    step9_lapb_timer()
    step10_lapb_config()
    return print_summary()


if __name__ == "__main__":
    sys.exit(main())
