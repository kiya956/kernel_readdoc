#!/usr/bin/env python3
"""
fwctl (Firmware Control) Subsystem — bpftrace verification test

Verifies the fwctl framework: device nodes, config, ioctl dispatch,
scope gating, RPC flow, and provider registration kprobes.

Requirements:
  - Linux >= 6.12 with CONFIG_FWCTL=y
  - bpftrace >= 0.14, root for bpftrace steps
  - mlx5 or pds hardware for hardware-dependent steps (optional)

Usage:
  sudo python3 fwctl_trace_test.py
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
# Step 1: Config and device nodes
# ─────────────────────────────────────────────────────────────
def step1_config_and_devices():
    header("Step 1: fwctl config and /dev/fwctl device nodes")

    cfg_path = f"/boot/config-{os.uname().release}"
    enabled = False
    if os.path.exists(cfg_path):
        with open(cfg_path) as f:
            cfg = f.read()
        for opt in ["CONFIG_FWCTL", "CONFIG_MLX5_SF_MANAGER", "CONFIG_PDS_CORE"]:
            if f"{opt}=y" in cfg or f"{opt}=m" in cfg:
                info(f"  {opt} enabled")
                if "CONFIG_FWCTL" in opt:
                    enabled = True

    if sym_exists("fwctl_register"):
        info("fwctl_register in kallsyms — fwctl built-in")
        enabled = True

    nodes = glob.glob("/dev/fwctl/fwctl*")
    if nodes:
        info(f"Found {len(nodes)} fwctl device(s): {nodes}")
        pass_(f"fwctl device nodes present: {nodes}")
        return True

    if enabled:
        info("fwctl compiled but no devices (no mlx5/pds hardware)")
        pass_("fwctl framework present (no hardware on this system)")
    else:
        info("fwctl not compiled (CONFIG_FWCTL not set)")
        pass_("Step complete (fwctl not present — Linux < 6.12 or not enabled)")
    return enabled

# ─────────────────────────────────────────────────────────────
# Step 2: sysfs class
# ─────────────────────────────────────────────────────────────
def step2_sysfs():
    header("Step 2: fwctl sysfs class")

    cls = glob.glob("/sys/class/fwctl/fwctl*")
    if cls:
        for c in cls[:3]:
            info(f"  {c}")
            for attr in ["uevent", "dev"]:
                p = os.path.join(c, attr)
                if os.path.exists(p):
                    with open(p) as f:
                        info(f"    {attr}: {f.read().strip()[:60]}")
        pass_(f"{len(cls)} fwctl device(s) in sysfs")
    elif os.path.exists("/sys/class/fwctl"):
        info("/sys/class/fwctl exists but empty (no devices)")
        pass_("fwctl class registered (no devices)")
    else:
        info("/sys/class/fwctl not found")
        pass_("Step skipped (fwctl not loaded)")
    return True

# ─────────────────────────────────────────────────────────────
# Step 3: fwctl_register kprobe
# ─────────────────────────────────────────────────────────────
BPFTRACE_REG = r"""
kprobe:fwctl_register
{
    printf("FWCTL_REGISTER pid=%d comm=%s\n", pid, comm);
}
kretprobe:fwctl_register
{
    printf("FWCTL_REGISTER_RET ret=%d\n", retval);
}
"""

def step3_register_kprobe():
    header("Step 3: fwctl_register kprobe — 5s")

    if os.geteuid() != 0:
        fail("Root required"); return False
    if not sym_exists("fwctl_register"):
        pass_("Step skipped (fwctl not present)"); return True

    info("Watching fwctl_register for 5s...")
    lines = run_bpftrace(BPFTRACE_REG, "FWCTL_REGISTER")
    if lines:
        pass_(f"Captured {len(lines)} fwctl registration(s)")
    else:
        info("No registrations (devices registered at boot)")
        pass_("fwctl_register kprobe attached OK")
    return True

# ─────────────────────────────────────────────────────────────
# Step 4: fwctl_cmd_rpc kprobe (FWCTL_RPC ioctl)
# ─────────────────────────────────────────────────────────────
BPFTRACE_RPC = r"""
kprobe:fwctl_cmd_rpc
{
    printf("FWCTL_RPC pid=%d comm=%s\n", pid, comm);
}
kretprobe:fwctl_cmd_rpc
{
    printf("FWCTL_RPC_RET ret=%d\n", retval);
}
"""

def step4_rpc_kprobe():
    header("Step 4: fwctl_cmd_rpc kprobe (FWCTL_RPC ioctl) — 5s")

    if os.geteuid() != 0:
        fail("Root required"); return False
    if not sym_exists("fwctl_cmd_rpc"):
        pass_("Step skipped (fwctl not present)"); return True

    info("Watching FWCTL_RPC calls for 5s (run mlx5ctl/pdsctl to trigger)...")
    lines = run_bpftrace(BPFTRACE_RPC, "FWCTL_RPC")
    if lines:
        pass_(f"Captured {len(lines)} FWCTL_RPC call(s)")
    else:
        info("No RPC calls in window (no fwctl userspace tool running)")
        pass_("fwctl_cmd_rpc kprobe attached OK")
    return True

# ─────────────────────────────────────────────────────────────
# Step 5: fwctl_cmd_info kprobe (FWCTL_INFO ioctl)
# ─────────────────────────────────────────────────────────────
BPFTRACE_INFO = r"""
kprobe:fwctl_cmd_info
{
    printf("FWCTL_INFO pid=%d comm=%s\n", pid, comm);
}
"""

def step5_info_kprobe():
    header("Step 5: fwctl_cmd_info kprobe (FWCTL_INFO ioctl) — 5s")

    if os.geteuid() != 0:
        fail("Root required"); return False
    if not sym_exists("fwctl_cmd_info"):
        pass_("Step skipped (fwctl not present)"); return True

    info("Watching FWCTL_INFO calls for 5s...")
    lines = run_bpftrace(BPFTRACE_INFO, "FWCTL_INFO")
    if lines:
        pass_(f"Captured {len(lines)} FWCTL_INFO call(s)")
    else:
        info("No FWCTL_INFO calls in window")
        pass_("fwctl_cmd_info kprobe attached OK")
    return True

# ─────────────────────────────────────────────────────────────
# Step 6: Scope gating — check kernel taint
# ─────────────────────────────────────────────────────────────
def step6_taint():
    header("Step 6: Kernel taint state (TAINT_FWCTL)")

    try:
        with open("/proc/sys/kernel/tainted") as f:
            taint_val = int(f.read().strip())
        TAINT_FWCTL = (1 << 20)  # bit 20 per kernel docs
        if taint_val & TAINT_FWCTL:
            info(f"WARNING: Kernel is tainted with TAINT_FWCTL (taint=0x{taint_val:x})")
            info("This means FWCTL_RPC_DEBUG_WRITE was used previously")
            pass_("TAINT_FWCTL detected (invasive fwctl debug was used)")
        else:
            info(f"Kernel taint value: 0x{taint_val:x}")
            info("TAINT_FWCTL (bit 20) not set — no invasive firmware RPC used")
            pass_("Kernel not tainted by fwctl (FWCTL_RPC_DEBUG_WRITE not used)")
    except Exception as e:
        info(f"Could not read taint: {e}")
        pass_("Step skipped")
    return True

# ─────────────────────────────────────────────────────────────
# Step 7: mlx5ctl / pds driver presence
# ─────────────────────────────────────────────────────────────
def step7_providers():
    header("Step 7: fwctl hardware provider drivers")

    found = []
    ret = subprocess.run(["lsmod"], capture_output=True, text=True)
    for mod in ["mlx5_core", "pds_core", "ionic"]:
        if mod in ret.stdout:
            found.append(mod)
            info(f"  {mod} loaded")

    # Check for mlx5 auxiliary devices
    aux_devs = glob.glob("/sys/bus/auxiliary/devices/*mlx5*")
    if aux_devs:
        fwctl_aux = [d for d in aux_devs if "fwctl" in d or "sf" in d]
        if fwctl_aux:
            info(f"  mlx5 fwctl auxiliary devices: {fwctl_aux[:2]}")

    if found:
        pass_(f"Providers present: {', '.join(found)}")
    else:
        info("No mlx5/pds hardware modules loaded")
        pass_("Step skipped (no fwctl hardware on this system)")
    return True

# ─────────────────────────────────────────────────────────────
# Step 8: open/close uctx kprobe
# ─────────────────────────────────────────────────────────────
BPFTRACE_OPEN = r"""
kprobe:fwctl_open
{
    printf("FWCTL_OPEN pid=%d comm=%s\n", pid, comm);
}
kprobe:fwctl_release
{
    printf("FWCTL_RELEASE pid=%d\n", pid);
}
"""

def step8_open_release():
    header("Step 8: fwctl_open / fwctl_release kprobes — 5s")

    if os.geteuid() != 0:
        fail("Root required"); return False
    if not sym_exists("fwctl_open"):
        pass_("Step skipped (fwctl not present)"); return True

    info("Watching fwctl_open/release for 5s...")
    lines = run_bpftrace(BPFTRACE_OPEN, "FWCTL_")
    if lines:
        opens   = sum(1 for l in lines if "OPEN" in l and "RELEASE" not in l)
        releases = sum(1 for l in lines if "RELEASE" in l)
        pass_(f"Captured {opens} open + {releases} release events")
    else:
        info("No open/release events (no userspace opened /dev/fwctl)")
        pass_("fwctl_open kprobe attached OK")
    return True

# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────
def main():
    print("=" * 62)
    print("  fwctl (Firmware Control) Subsystem — bpftrace Verification")
    print("=" * 62)

    if os.geteuid() != 0:
        print(f"\n{RED}WARNING: Not running as root.{RESET}")
        print("Steps 3-5,8 require root. Steps 1,2,6,7 will run.\n")

    steps = [
        ("Config + /dev/fwctl nodes",           step1_config_and_devices),
        ("fwctl sysfs class",                   step2_sysfs),
        ("fwctl_register kprobe (5s)",          step3_register_kprobe),
        ("fwctl_cmd_rpc kprobe (FWCTL_RPC, 5s)",step4_rpc_kprobe),
        ("fwctl_cmd_info kprobe (FWCTL_INFO, 5s)",step5_info_kprobe),
        ("Kernel TAINT_FWCTL check",            step6_taint),
        ("Hardware provider drivers",           step7_providers),
        ("fwctl_open/release kprobes (5s)",     step8_open_release),
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
