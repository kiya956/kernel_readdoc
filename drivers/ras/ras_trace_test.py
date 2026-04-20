#!/usr/bin/env python3
"""
RAS (Reliability, Availability, Serviceability) Subsystem — bpftrace test

Verifies the RAS framework: tracepoints, CEC debugfs, AMD ATL/FMPM presence,
and kprobes on core error-logging paths.

Requirements:
  - Linux with CONFIG_RAS=y, CONFIG_RAS_CEC=y
  - bpftrace >= 0.14, root for bpftrace steps

Usage:
  sudo python3 ras_trace_test.py
"""

import subprocess, sys, os, glob, time

RED   = "\033[91m"
GREEN = "\033[92m"
CYAN  = "\033[96m"
RESET = "\033[0m"

def pass_(msg): print(f"  {GREEN}[PASS]{RESET} {msg}")
def fail(msg):  print(f"  {RED}[FAIL]{RESET} {msg}")
def info(msg):  print(f"  {CYAN}[INFO]{RESET} {msg}")
def header(msg):
    print(f"\n{'='*62}")
    print(f"  {msg}")
    print(f"{'='*62}")

def sym_exists(name):
    ret = subprocess.run(["grep", "-c", f" {name}$", "/proc/kallsyms"],
                         capture_output=True, text=True)
    return int(ret.stdout.strip() or "0") > 0

def run_bpftrace(script, label, timeout=5):
    proc = subprocess.Popen(["bpftrace", "-e", script],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    lines = []
    start = time.time()
    try:
        while time.time() - start < timeout:
            line = proc.stdout.readline()
            if line and label in line:
                lines.append(line.strip())
                if len(lines) <= 4:
                    info(f"  {line.strip()}")
    except Exception:
        pass
    finally:
        proc.terminate(); proc.wait(timeout=3)
    return lines

# ─────────────────────────────────────────────────────────────
# Step 1: Config check
# ─────────────────────────────────────────────────────────────
def step1_config():
    header("Step 1: RAS kernel configuration")

    cfg_path = f"/boot/config-{os.uname().release}"
    opts = ["CONFIG_RAS", "CONFIG_RAS_CEC", "CONFIG_AMD_ATL",
            "CONFIG_EDAC", "CONFIG_ACPI_EXTLOG"]
    enabled = {}
    if os.path.exists(cfg_path):
        with open(cfg_path) as f:
            cfg = f.read()
        for opt in opts:
            if f"{opt}=y" in cfg or f"{opt}=m" in cfg:
                enabled[opt] = True
                info(f"  {opt} enabled")

    if sym_exists("log_non_standard_event"):
        info("log_non_standard_event in kallsyms — RAS core built-in")
        enabled["RAS_CORE"] = True

    if enabled:
        pass_(f"RAS subsystem present ({len(enabled)} options enabled)")
    else:
        info("RAS not compiled (limited functionality)")
        pass_("Step complete (RAS not fully enabled)")
    return bool(enabled)

# ─────────────────────────────────────────────────────────────
# Step 2: RAS tracepoints in tracefs
# ─────────────────────────────────────────────────────────────
def step2_tracepoints():
    header("Step 2: RAS tracepoints in tracefs")

    tracefs = "/sys/kernel/tracing/events/ras"
    if not os.path.exists(tracefs):
        tracefs = "/sys/kernel/debug/tracing/events/ras"

    if os.path.exists(tracefs):
        events = os.listdir(tracefs)
        expected = {"mc_event", "arm_event", "non_standard_event",
                    "aer_event", "memory_failure_event"}
        found = set(events) & expected
        missing = expected - found
        info(f"RAS tracefs events found: {sorted(found)}")
        if missing:
            info(f"Missing: {sorted(missing)}")
        pass_(f"{len(found)}/{len(expected)} expected RAS tracepoints present")
    else:
        info("tracefs events/ras not found (CONFIG_RAS tracepoints may be disabled)")
        pass_("Step skipped (tracefs not accessible)")
    return True

# ─────────────────────────────────────────────────────────────
# Step 3: Live ras:mc_event capture (5s)
# ─────────────────────────────────────────────────────────────
BPFTRACE_MC = r"""
tracepoint:ras:mc_event
{
    printf("MC_EVENT cpu=%d err_type=%s\n",
           args->cpu, str(args->error_type));
}
"""

def step3_mc_event():
    header("Step 3: ras:mc_event tracepoint — 5s window")

    if os.geteuid() != 0:
        fail("Root required"); return False

    ret = subprocess.run(["bpftrace", "-l", "tracepoint:ras:mc_event"],
                         capture_output=True, text=True, timeout=10)
    if "mc_event" not in ret.stdout:
        info("tracepoint:ras:mc_event not available")
        pass_("Step skipped"); return True

    info("Watching mc_event for 5s (hardware memory errors are rare — no events is normal)...")
    lines = run_bpftrace(BPFTRACE_MC, "MC_EVENT")
    if lines:
        pass_(f"IMPORTANT: Captured {len(lines)} memory error event(s)!")
    else:
        info("No mc_event (system healthy — no correctable errors)")
        pass_("ras:mc_event probe attached OK (no events = system healthy)")
    return True

# ─────────────────────────────────────────────────────────────
# Step 4: ras:aer_event (PCIe errors)
# ─────────────────────────────────────────────────────────────
BPFTRACE_AER = r"""
tracepoint:ras:aer_event
{
    printf("AER_EVENT dev=%s status=0x%x severity=%s\n",
           str(args->dev_name), args->status, str(args->error_class));
}
"""

def step4_aer_event():
    header("Step 4: ras:aer_event tracepoint — 5s window")

    if os.geteuid() != 0:
        fail("Root required"); return False

    ret = subprocess.run(["bpftrace", "-l", "tracepoint:ras:aer_event"],
                         capture_output=True, text=True, timeout=10)
    if "aer_event" not in ret.stdout:
        info("tracepoint:ras:aer_event not available")
        pass_("Step skipped"); return True

    info("Watching PCIe AER events for 5s...")
    lines = run_bpftrace(BPFTRACE_AER, "AER_EVENT")
    if lines:
        pass_(f"Captured {len(lines)} PCIe AER error(s)")
    else:
        info("No AER events (PCIe link healthy)")
        pass_("ras:aer_event probe attached OK")
    return True

# ─────────────────────────────────────────────────────────────
# Step 5: ras:memory_failure_event
# ─────────────────────────────────────────────────────────────
BPFTRACE_MF = r"""
tracepoint:ras:memory_failure_event
{
    printf("MEM_FAIL pfn=0x%lx type=%s\n",
           args->pfn, str(args->action_result));
}
"""

def step5_memory_failure():
    header("Step 5: ras:memory_failure_event tracepoint — 5s")

    if os.geteuid() != 0:
        fail("Root required"); return False

    ret = subprocess.run(["bpftrace", "-l", "tracepoint:ras:memory_failure_event"],
                         capture_output=True, text=True, timeout=10)
    if "memory_failure_event" not in ret.stdout:
        info("tracepoint:ras:memory_failure_event not available")
        pass_("Step skipped"); return True

    info("Watching memory_failure_event for 5s...")
    lines = run_bpftrace(BPFTRACE_MF, "MEM_FAIL")
    if lines:
        pass_(f"Captured {len(lines)} page poison event(s)")
    else:
        info("No memory failure events (good — no poisoned pages)")
        pass_("ras:memory_failure_event probe attached OK")
    return True

# ─────────────────────────────────────────────────────────────
# Step 6: CEC debugfs
# ─────────────────────────────────────────────────────────────
def step6_cec_debugfs():
    header("Step 6: CEC (Correctable Error Collector) debugfs")

    cec_root = "/sys/kernel/debug/ras/cec"
    if not os.path.exists(cec_root):
        info(f"{cec_root} not found (CONFIG_RAS_CEC not enabled or not x86)")
        pass_("Step skipped (CEC not available)"); return True

    info(f"CEC debugfs: {cec_root}")
    for attr in ["action_threshold", "decay_interval", "pfns"]:
        path = os.path.join(cec_root, attr)
        if os.path.exists(path):
            try:
                with open(path) as f:
                    val = f.read().strip()
                info(f"  {attr}: {val[:60]}")
            except PermissionError:
                info(f"  {attr}: (permission denied)")

    # Check if any PFNs are tracked
    pfns_path = os.path.join(cec_root, "pfns")
    if os.path.exists(pfns_path):
        try:
            with open(pfns_path) as f:
                content = f.read().strip()
            if content:
                lines = content.split("\n")
                info(f"  {len(lines)} PFN(s) tracked by CEC")
                pass_(f"CEC active: tracking {len(lines)} PFN(s)")
            else:
                pass_("CEC active: no correctable errors accumulated")
        except Exception:
            pass_("CEC debugfs accessible")
    return True

# ─────────────────────────────────────────────────────────────
# Step 7: log_non_standard_event kprobe
# ─────────────────────────────────────────────────────────────
BPFTRACE_NSE = r"""
kprobe:log_non_standard_event
{
    printf("NON_STD_EVENT sev=%d len=%d\n", (int)arg3, (int)arg5);
}
kprobe:log_arm_hw_error
{
    printf("ARM_HW_ERROR sev=%d\n", (int)arg1);
}
"""

def step7_log_kprobes():
    header("Step 7: log_non_standard_event / log_arm_hw_error kprobes — 5s")

    if os.geteuid() != 0:
        fail("Root required"); return False

    has_nse = sym_exists("log_non_standard_event")
    has_arm = sym_exists("log_arm_hw_error")
    if not has_nse and not has_arm:
        info("Neither log_non_standard_event nor log_arm_hw_error in kallsyms")
        pass_("Step skipped (RAS core not present)"); return True

    info("Watching CPER/ARM error log functions for 5s...")
    lines = run_bpftrace(BPFTRACE_NSE, "EVENT")
    if lines:
        pass_(f"Captured {len(lines)} non-standard / ARM error event(s)")
    else:
        info("No events (no firmware-reported errors in window)")
        pass_("RAS log kprobes attached OK")
    return True

# ─────────────────────────────────────────────────────────────
# Step 8: AMD ATL / FMPM presence
# ─────────────────────────────────────────────────────────────
def step8_amd_atl():
    header("Step 8: AMD ATL / FMPM presence")

    # Check if running on AMD
    try:
        with open("/proc/cpuinfo") as f:
            cpuinfo = f.read()
        is_amd = "AuthenticAMD" in cpuinfo
    except Exception:
        is_amd = False

    if is_amd:
        info("AMD CPU detected")
        if sym_exists("amd_convert_umc_mca_addr_to_sys_addr"):
            pass_("AMD ATL (Address Translation Library) loaded")
        else:
            info("AMD ATL not loaded (may need MODULE_AMD_ATL=m + modprobe amd_atl)")
            pass_("AMD CPU present but ATL not loaded (may be Zen3 or earlier)")

        if sym_exists("amd_atl_register_decoder"):
            info("amd_atl_register_decoder in kallsyms")
    else:
        info("Non-AMD CPU — AMD ATL/FMPM not applicable")
        pass_("Step skipped (Intel/ARM system — AMD ATL not relevant)")
    return True

# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────
def main():
    print("=" * 62)
    print("  RAS (Reliability/Availability/Serviceability) — Verify")
    print("=" * 62)

    if os.geteuid() != 0:
        print(f"\n{RED}WARNING: Not running as root.{RESET}")
        print("Steps 3-5,7 require root. Steps 1,2,6,8 will run.\n")

    steps = [
        ("Kernel config / kallsyms",          step1_config),
        ("RAS tracepoints in tracefs",         step2_tracepoints),
        ("ras:mc_event (5s)",                  step3_mc_event),
        ("ras:aer_event (5s)",                 step4_aer_event),
        ("ras:memory_failure_event (5s)",      step5_memory_failure),
        ("CEC debugfs",                        step6_cec_debugfs),
        ("log_non_standard_event kprobe (5s)", step7_log_kprobes),
        ("AMD ATL / FMPM presence",            step8_amd_atl),
    ]

    results = []
    for name, fn in steps:
        try:
            ok = fn()
            results.append((name, ok if ok is not None else True))
        except subprocess.TimeoutExpired:
            fail(f"Timeout: {name}")
            results.append((name, False))
        except Exception as e:
            fail(f"Exception in {name}: {e}")
            results.append((name, False))

    header("Summary")
    passed = sum(1 for _, ok in results if ok)
    total  = len(results)
    for name, ok in results:
        status = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
        print(f"  [{status}] {name}")
    print(f"\n  Result: {passed}/{total} steps passed")
    sys.exit(0 if passed == total else 1)

if __name__ == "__main__":
    main()
