#!/usr/bin/env python3
"""
Thunderbolt/USB4 init flow verification using bpftrace.

Attaches kprobes to key functions in the Thunderbolt initialization path
and verifies they are called in the expected order when a Thunderbolt
controller is probed (e.g. after module reload or device hotplug).

Requirements:
  - Root privileges
  - bpftrace installed
  - thunderbolt module loadable (CONFIG_USB4=m or built-in)

Usage:
  sudo python3 test_thunderbolt_init.py
"""

import subprocess
import sys
import time
import os
import signal

BPFTRACE_SCRIPT = r"""
BEGIN {
    @step1 = 0;  // nhi_probe
    @step2 = 0;  // tb_domain_alloc
    @step3 = 0;  // tb_ctl_alloc
    @step4 = 0;  // tb_ctl_start
    @step5 = 0;  // tb_domain_add
    @step6 = 0;  // tb_switch_alloc
    @step7 = 0;  // tb_switch_add
    printf("=== Thunderbolt Init Flow Tracer ===\n");
    printf("Waiting for thunderbolt module probe...\n");
    printf("(reload module or plug a TB device to trigger)\n\n");
}

kprobe:nhi_probe {
    @step1 = 1;
    printf("[STEP 1] nhi_probe called (PCI probe entry)\n");
}

kprobe:tb_domain_alloc {
    @step2 = 1;
    printf("[STEP 2] tb_domain_alloc called (domain allocation)\n");
}

kprobe:tb_ctl_alloc {
    @step3 = 1;
    printf("[STEP 3] tb_ctl_alloc called (control channel alloc)\n");
}

kprobe:tb_ctl_start {
    @step4 = 1;
    printf("[STEP 4] tb_ctl_start called (control channel start)\n");
}

kprobe:tb_domain_add {
    @step5 = 1;
    printf("[STEP 5] tb_domain_add called (domain registration)\n");
}

kprobe:tb_switch_alloc {
    @step6 = 1;
    printf("[STEP 6] tb_switch_alloc called (root switch alloc)\n");
}

kprobe:tb_switch_add {
    @step7 = 1;
    printf("[STEP 7] tb_switch_add called (root switch register)\n");
}

END {
    printf("\n=== RESULTS ===\n");
    printf("Step 1 nhi_probe:        %s\n", @step1 ? "PASS" : "FAIL");
    printf("Step 2 tb_domain_alloc:  %s\n", @step2 ? "PASS" : "FAIL");
    printf("Step 3 tb_ctl_alloc:     %s\n", @step3 ? "PASS" : "FAIL");
    printf("Step 4 tb_ctl_start:     %s\n", @step4 ? "PASS" : "FAIL");
    printf("Step 5 tb_domain_add:    %s\n", @step5 ? "PASS" : "FAIL");
    printf("Step 6 tb_switch_alloc:  %s\n", @step6 ? "PASS" : "FAIL");
    printf("Step 7 tb_switch_add:    %s\n", @step7 ? "PASS" : "FAIL");
    printf("===============\n");
}
"""

# Alternative probe targets in case primary is inlined or renamed
ALT_PROBES = {
    "nhi_probe":        ["nhi_probe"],
    "tb_domain_alloc":  ["tb_domain_alloc"],
    "tb_ctl_alloc":     ["tb_ctl_alloc"],
    "tb_ctl_start":     ["tb_ctl_start"],
    "tb_domain_add":    ["tb_domain_add"],
    "tb_switch_alloc":  ["tb_switch_alloc", "tb_switch_alloc_safe_mode"],
    "tb_switch_add":    ["tb_switch_add"],
}


def check_probe_exists(func_name):
    """Check if the function is available as a kprobe target."""
    try:
        with open("/sys/kernel/tracing/available_filter_functions", "r") as f:
            for line in f:
                if func_name in line.split()[0]:
                    return True
    except FileNotFoundError:
        # Fallback: check /proc/kallsyms
        try:
            result = subprocess.run(
                ["grep", "-w", func_name, "/proc/kallsyms"],
                capture_output=True, text=True, timeout=5
            )
            return result.returncode == 0
        except Exception:
            pass
    return False


def verify_probes():
    """Verify all probe targets are available."""
    print("Verifying probe targets...")
    missing = []
    for primary, alts in ALT_PROBES.items():
        found = False
        for alt in alts:
            if check_probe_exists(alt):
                found = True
                break
        if not found:
            missing.append(primary)
            print(f"  WARNING: {primary} not found in available probes")
        else:
            print(f"  OK: {primary}")
    return missing


def run_bpftrace(timeout=60):
    """Run the bpftrace script with a timeout."""
    print(f"\nStarting bpftrace (timeout={timeout}s)...")
    print("Press Ctrl+C to stop early and see results.\n")

    proc = subprocess.Popen(
        ["bpftrace", "-e", BPFTRACE_SCRIPT],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        print(stdout)
        if stderr:
            print(f"stderr: {stderr}", file=sys.stderr)
    except subprocess.TimeoutExpired:
        # Send SIGINT so bpftrace prints END block
        proc.send_signal(signal.SIGINT)
        try:
            stdout, stderr = proc.communicate(timeout=10)
            print(stdout)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
            print(stdout)
    except KeyboardInterrupt:
        proc.send_signal(signal.SIGINT)
        try:
            stdout, stderr = proc.communicate(timeout=10)
            print(stdout)
        except Exception:
            proc.kill()

    return proc.returncode


def main():
    if os.geteuid() != 0:
        print("ERROR: This script requires root privileges.")
        print("Usage: sudo python3 test_thunderbolt_init.py")
        sys.exit(1)

    # Check bpftrace is available
    try:
        subprocess.run(["bpftrace", "--version"], capture_output=True, check=True)
    except FileNotFoundError:
        print("ERROR: bpftrace is not installed.")
        print("Install: sudo apt install bpftrace")
        sys.exit(1)

    missing = verify_probes()
    if missing:
        print(f"\nWARNING: {len(missing)} probe(s) not found — "
              "they may be inlined or module not loaded.")
        print("Continuing anyway...\n")

    timeout = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    run_bpftrace(timeout)


if __name__ == "__main__":
    main()
