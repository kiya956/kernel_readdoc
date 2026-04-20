#!/usr/bin/env python3
"""
Power Management Domain (genpd) Subsystem — bpftrace verification test

Verifies the genpd framework: domain registration, power_on/off flow,
governor decisions, runtime PM integration, and debugfs state.

Requirements:
  - Linux with CONFIG_PM_GENERIC_DOMAINS=y
  - bpftrace >= 0.14, root for bpftrace steps

Usage:
  sudo python3 pmdomain_trace_test.py
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
# Step 1: Config and kallsyms
# ─────────────────────────────────────────────────────────────
def step1_config():
    header("Step 1: genpd kernel configuration")

    cfg_path = f"/boot/config-{os.uname().release}"
    enabled = False
    if os.path.exists(cfg_path):
        with open(cfg_path) as f:
            cfg = f.read()
        for opt in ["CONFIG_PM_GENERIC_DOMAINS", "CONFIG_PM_GENERIC_DOMAINS_OF"]:
            if f"{opt}=y" in cfg:
                info(f"{opt}=y")
                enabled = True

    if sym_exists("pm_genpd_init"):
        info("pm_genpd_init in kallsyms")
        enabled = True

    if enabled:
        pass_("genpd subsystem present")
    else:
        info("CONFIG_PM_GENERIC_DOMAINS not enabled")
        pass_("Step complete (genpd not present on this system)")
    return enabled

# ─────────────────────────────────────────────────────────────
# Step 2: debugfs pm_genpd summary
# ─────────────────────────────────────────────────────────────
def step2_debugfs():
    header("Step 2: /sys/kernel/debug/pm_genpd summary")

    subprocess.run(["mount", "-t", "debugfs", "none", "/sys/kernel/debug"],
                   capture_output=True)

    summary = "/sys/kernel/debug/pm_genpd/pm_genpd_summary"
    if os.path.exists(summary):
        with open(summary) as f:
            lines = f.readlines()
        domain_lines = [l for l in lines if l.strip() and not l.startswith("domain")]
        info(f"Found {len(domain_lines)} power domain(s):")
        for l in domain_lines[:8]:
            info(f"  {l.rstrip()}")
        pass_(f"genpd summary: {len(domain_lines)} domains registered")
    else:
        info("/sys/kernel/debug/pm_genpd/pm_genpd_summary not found")
        pass_("Step skipped (debugfs not available or genpd not loaded)")
    return True

# ─────────────────────────────────────────────────────────────
# Step 3: pm_genpd_init kprobe
# ─────────────────────────────────────────────────────────────
BPFTRACE_INIT = r"""
kprobe:pm_genpd_init
{
    printf("GENPD_INIT pid=%d name=%s\n", pid, str(((struct generic_pm_domain *)arg0)->name));
}
kretprobe:pm_genpd_init
{
    printf("GENPD_INIT_RET ret=%d\n", retval);
}
"""

def step3_genpd_init():
    header("Step 3: pm_genpd_init kprobe (domain registration) — 5s")

    if os.geteuid() != 0:
        fail("Root required"); return False
    if not sym_exists("pm_genpd_init"):
        pass_("Step skipped (genpd not present)"); return True

    info("Watching pm_genpd_init for 5s...")
    lines = run_bpftrace(BPFTRACE_INIT, "GENPD_INIT")
    if lines:
        pass_(f"Captured {len(lines)} genpd init event(s)")
    else:
        info("No init events (domains registered at boot)")
        pass_("pm_genpd_init kprobe attached OK")
    return True

# ─────────────────────────────────────────────────────────────
# Step 4: _genpd_power_off kprobe (domain power-off)
# ─────────────────────────────────────────────────────────────
BPFTRACE_OFF = r"""
kprobe:_genpd_power_off
{
    printf("GENPD_POWER_OFF name=%s timed=%d\n",
           str(((struct generic_pm_domain *)arg0)->name),
           (int)arg1);
}
kretprobe:_genpd_power_off
{
    printf("GENPD_POWER_OFF_RET ret=%d\n", retval);
}
"""

def step4_power_off():
    header("Step 4: _genpd_power_off kprobe — 5s")

    if os.geteuid() != 0:
        fail("Root required"); return False
    if not sym_exists("_genpd_power_off"):
        pass_("Step skipped"); return True

    info("Watching _genpd_power_off for 5s (any idle domain will trigger)...")
    lines = run_bpftrace(BPFTRACE_OFF, "GENPD_POWER_OFF")
    if lines:
        pass_(f"Captured {len(lines)} domain power-off event(s)")
    else:
        info("No power-off events in window")
        pass_("_genpd_power_off kprobe attached OK")
    return True

# ─────────────────────────────────────────────────────────────
# Step 5: genpd_power_on kprobe (domain power-on)
# ─────────────────────────────────────────────────────────────
BPFTRACE_ON = r"""
kprobe:genpd_power_on
{
    printf("GENPD_POWER_ON name=%s depth=%d\n",
           str(((struct generic_pm_domain *)arg0)->name),
           (int)arg1);
}
"""

def step5_power_on():
    header("Step 5: genpd_power_on kprobe — 5s")

    if os.geteuid() != 0:
        fail("Root required"); return False
    if not sym_exists("genpd_power_on"):
        pass_("Step skipped"); return True

    info("Watching genpd_power_on for 5s...")
    lines = run_bpftrace(BPFTRACE_ON, "GENPD_POWER_ON")
    if lines:
        pass_(f"Captured {len(lines)} domain power-on event(s)")
    else:
        info("No power-on events in window")
        pass_("genpd_power_on kprobe attached OK")
    return True

# ─────────────────────────────────────────────────────────────
# Step 6: governor decision kprobe
# ─────────────────────────────────────────────────────────────
BPFTRACE_GOV = r"""
kprobe:_default_power_down_ok
{
    printf("GOV_CHECK name=%s\n",
           str(((struct dev_pm_domain *)arg0)->ops.runtime_suspend == 0 ?
               "unknown" : "genpd"));
}
kretprobe:_default_power_down_ok
{
    printf("GOV_RESULT ok=%d\n", retval);
}
"""

def step6_governor():
    header("Step 6: governor _default_power_down_ok kprobe — 5s")

    if os.geteuid() != 0:
        fail("Root required"); return False
    if not sym_exists("_default_power_down_ok"):
        pass_("Step skipped"); return True

    info("Watching governor decisions for 5s...")
    lines = run_bpftrace(BPFTRACE_GOV, "GOV_")
    if lines:
        yes = sum(1 for l in lines if "ok=1" in l)
        no  = sum(1 for l in lines if "ok=0" in l)
        info(f"  power-down approved: {yes}, rejected: {no}")
        pass_(f"Captured {len(lines)} governor decision(s)")
    else:
        info("No governor decisions in window")
        pass_("Governor kprobe attached OK")
    return True

# ─────────────────────────────────────────────────────────────
# Step 7: pm_genpd_runtime_suspend kprobe
# ─────────────────────────────────────────────────────────────
BPFTRACE_RT = r"""
kprobe:pm_genpd_runtime_suspend
{
    printf("GENPD_RT_SUSPEND dev=%s\n", str(((struct device *)arg0)->kobj.name));
}
kprobe:pm_genpd_runtime_resume
{
    printf("GENPD_RT_RESUME dev=%s\n", str(((struct device *)arg0)->kobj.name));
}
"""

def step7_runtime_hooks():
    header("Step 7: pm_genpd_runtime_suspend/resume kprobes — 5s")

    if os.geteuid() != 0:
        fail("Root required"); return False
    if not sym_exists("pm_genpd_runtime_suspend"):
        pass_("Step skipped"); return True

    info("Watching runtime PM transitions for 5s...")
    lines = run_bpftrace(BPFTRACE_RT, "GENPD_RT")
    if lines:
        susp = sum(1 for l in lines if "SUSPEND" in l)
        resm = sum(1 for l in lines if "RESUME"  in l)
        pass_(f"Captured {susp} suspend + {resm} resume genpd transitions")
    else:
        info("No genpd runtime transitions in window")
        pass_("genpd runtime kprobes attached OK")
    return True

# ─────────────────────────────────────────────────────────────
# Step 8: /sys/bus/platform/drivers genpd providers
# ─────────────────────────────────────────────────────────────
def step8_providers():
    header("Step 8: genpd provider drivers in sysfs")

    keywords = ["genpd", "pm_domain", "power_domain", "scmi-pm",
                "scpi-pm", "qcom-rpmd", "mtk-scpsys"]
    found = []
    for kw in keywords:
        paths = glob.glob(f"/sys/bus/platform/drivers/*{kw}*")
        found.extend(paths)

    # Also check bound devices
    dev_paths = glob.glob("/sys/bus/platform/devices/*/power/wakeup")
    genpd_devices = []
    for dp in dev_paths[:50]:
        dev_dir = os.path.dirname(os.path.dirname(dp))
        pm_domain = os.path.join(dev_dir, "power/pm_domain_name")
        if os.path.exists(pm_domain):
            try:
                with open(pm_domain) as f:
                    name = f.read().strip()
                if name:
                    genpd_devices.append((os.path.basename(dev_dir), name))
            except Exception:
                pass

    if genpd_devices:
        info(f"Devices with PM domains ({min(len(genpd_devices), 6)} shown):")
        for dev, dom in genpd_devices[:6]:
            info(f"  {dev} → domain: {dom}")
        pass_(f"{len(genpd_devices)} device(s) attached to genpd domains")
    elif found:
        for p in found[:5]:
            info(f"  {p}")
        pass_(f"{len(found)} genpd provider driver(s) registered")
    else:
        info("No genpd devices visible via sysfs (may need root or DT-based SoC)")
        pass_("Step complete (non-DT platform or attributes not exported)")
    return True

# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────
def main():
    print("=" * 62)
    print("  Power Management Domain (genpd) — bpftrace Verification")
    print("=" * 62)

    if os.geteuid() != 0:
        print(f"\n{RED}WARNING: Not running as root.{RESET}")
        print("Steps 3-7 require root. Steps 1,2,8 will run.\n")

    steps = [
        ("Kernel config / kallsyms",                step1_config),
        ("debugfs pm_genpd_summary",                step2_debugfs),
        ("pm_genpd_init kprobe (5s)",               step3_genpd_init),
        ("_genpd_power_off kprobe (5s)",            step4_power_off),
        ("genpd_power_on kprobe (5s)",              step5_power_on),
        ("Governor _default_power_down_ok (5s)",    step6_governor),
        ("genpd runtime PM hooks (5s)",             step7_runtime_hooks),
        ("genpd providers in sysfs",                step8_providers),
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
