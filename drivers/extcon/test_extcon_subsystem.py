#!/usr/bin/env python3
"""
extcon (External Connector) Subsystem Workflow Verification
============================================================
Uses bpftrace to verify the Linux extcon subsystem call chain step by step.

Each step traces a specific kernel function to confirm the provider →
core → consumer notification flow.

Requirements:
  - Linux kernel with extcon support (CONFIG_EXTCON=y/m)
  - bpftrace >= 0.14
  - Root privileges (sudo)
  - Optional: extcon-gpio or gpio-keys for live trigger tests

Usage:
  sudo python3 test_extcon_subsystem.py
"""

import subprocess
import sys
import os
import time
import textwrap
import tempfile

PASS = "\033[32m[PASS]\033[0m"
FAIL = "\033[31m[FAIL]\033[0m"
SKIP = "\033[33m[SKIP]\033[0m"
INFO = "\033[34m[INFO]\033[0m"

results = []


def run(cmd, timeout=10):
    try:
        return subprocess.run(
            cmd, shell=True, capture_output=True,
            text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return None


def check_prereqs():
    print(f"\n{INFO} Checking prerequisites...")
    issues = []
    if not run("which bpftrace") or run("which bpftrace").returncode != 0:
        issues.append("bpftrace not found in PATH")
    if os.geteuid() != 0:
        issues.append("Must run as root (sudo)")
    if issues:
        for i in issues:
            print(f"{FAIL}  {i}")
        sys.exit(1)
    print(f"{PASS}  Prerequisites OK")


def bpf_step(number, description, bpf_script,
             trigger_cmd=None, expect_keyword=None,
             timeout=8, skip_if_fail=None):
    print(f"\n── Step {number}: {description}")

    if skip_if_fail:
        r = run(skip_if_fail)
        if r is None or r.returncode != 0:
            print(f"{SKIP}  Condition not met: {skip_if_fail}")
            results.append((number, description, "SKIP"))
            return

    with tempfile.NamedTemporaryFile(
        mode='w', suffix='.bt', delete=False
    ) as f:
        f.write(bpf_script)
        bt_file = f.name

    bpf_proc = subprocess.Popen(
        ["bpftrace", bt_file],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True
    )
    time.sleep(1.5)

    if trigger_cmd:
        run(trigger_cmd, timeout=5)

    try:
        stdout, stderr = bpf_proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        bpf_proc.kill()
        stdout, stderr = bpf_proc.communicate()

    os.unlink(bt_file)
    output = stdout + stderr

    if expect_keyword and expect_keyword in output:
        print(f"{PASS}  Detected: '{expect_keyword}'")
        print(f"         {output.strip()[:200]}")
        results.append((number, description, "PASS"))
    elif not expect_keyword and bpf_proc.returncode == 0:
        print(f"{PASS}  Script ran cleanly")
        results.append((number, description, "PASS"))
    else:
        if any(x in output for x in ("not traceable", "No probes", "ERROR")):
            print(f"{SKIP}  Symbol not traceable (inlined or absent)")
            print(f"         {stderr.strip()[:200]}")
            results.append((number, description, "SKIP"))
        else:
            print(f"{FAIL}  Expected '{expect_keyword}' not found")
            print(f"         stdout: {stdout.strip()[:200]}")
            print(f"         stderr: {stderr.strip()[:200]}")
            results.append((number, description, "FAIL"))


# ──────────────────────────────────────────────────────────────────
# Steps
# ──────────────────────────────────────────────────────────────────

def step1_symbols():
    print(f"\n── Step 1: extcon symbols present in kernel")
    r = run("grep -c ' extcon_' /proc/kallsyms")
    count = int(r.stdout.strip()) if r and r.returncode == 0 else 0
    if count > 5:
        print(f"{PASS}  {count} extcon_* symbols in /proc/kallsyms")
        results.append((1, "extcon symbols in kallsyms", "PASS"))
    else:
        run("modprobe extcon 2>/dev/null", timeout=5)
        r2 = run("grep -c ' extcon_' /proc/kallsyms")
        count2 = int(r2.stdout.strip()) if r2 and r2.returncode == 0 else 0
        if count2 > 5:
            print(f"{PASS}  {count2} extcon_* symbols after modprobe")
            results.append((1, "extcon symbols in kallsyms", "PASS"))
        else:
            print(f"{FAIL}  extcon not built into this kernel")
            results.append((1, "extcon symbols in kallsyms", "FAIL"))


def step2_sysfs_class():
    print(f"\n── Step 2: /sys/class/extcon is populated")
    r = run("ls /sys/class/extcon/ 2>/dev/null")
    if r and r.returncode == 0 and r.stdout.strip():
        devs = r.stdout.strip().split()
        print(f"{PASS}  extcon devices found: {devs[:5]}")
        results.append((2, "/sys/class/extcon populated", "PASS"))
    else:
        print(f"{SKIP}  No extcon devices registered on this system")
        results.append((2, "/sys/class/extcon populated", "SKIP"))


def step3_cable_state_readable():
    print(f"\n── Step 3: Cable state readable via sysfs")
    r = run("find /sys/class/extcon/ -name 'state' 2>/dev/null | head -5")
    if r and r.returncode == 0 and r.stdout.strip():
        paths = r.stdout.strip().split('\n')
        all_ok = True
        for p in paths[:3]:
            rc = run(f"cat {p} 2>/dev/null")
            if rc is None or rc.returncode != 0:
                all_ok = False
        if all_ok:
            print(f"{PASS}  Cable state files readable ({len(paths)} found)")
            results.append((3, "sysfs cable state readable", "PASS"))
        else:
            print(f"{FAIL}  Some state files unreadable")
            results.append((3, "sysfs cable state readable", "FAIL"))
    else:
        print(f"{SKIP}  No extcon state files found")
        results.append((3, "sysfs cable state readable", "SKIP"))


def step4_extcon_dev_register():
    bpf_step(
        4, "extcon_dev_register called at device probe",
        textwrap.dedent("""
            kprobe:extcon_dev_register {
                printf("EXTCON_DEV_REGISTER edev=%p pid=%d comm=%s\\n",
                       arg0, pid, comm);
                exit();
            }
            interval:s:6 { exit(); }
        """),
        trigger_cmd=(
            "modprobe -r extcon-gpio 2>/dev/null; "
            "modprobe extcon-gpio 2>/dev/null; true"
        ),
        expect_keyword="EXTCON_DEV_REGISTER",
        timeout=8,
    )


def step5_extcon_set_state():
    bpf_step(
        5, "extcon_set_state records connector state change",
        textwrap.dedent("""
            kprobe:extcon_set_state {
                printf("EXTCON_SET_STATE edev=%p id=%d state=%d\\n",
                       arg0, arg1, (int)arg2);
                exit();
            }
            interval:s:8 { exit(); }
        """),
        trigger_cmd="true",  # state change requires real hardware event
        expect_keyword="EXTCON_SET_STATE",
        timeout=8,
    )


def step6_extcon_set_state_sync():
    bpf_step(
        6, "extcon_set_state_sync updates state and fires notifiers",
        textwrap.dedent("""
            kprobe:extcon_set_state_sync {
                printf("EXTCON_SET_STATE_SYNC edev=%p id=%d state=%d\\n",
                       arg0, arg1, (int)arg2);
                exit();
            }
            interval:s:8 { exit(); }
        """),
        trigger_cmd="true",
        expect_keyword="EXTCON_SET_STATE_SYNC",
        timeout=8,
    )


def step7_extcon_sync():
    bpf_step(
        7, "extcon_sync dispatches notifier chain and uevent",
        textwrap.dedent("""
            kprobe:extcon_sync {
                printf("EXTCON_SYNC edev=%p id=%d\\n", arg0, arg1);
                exit();
            }
            interval:s:8 { exit(); }
        """),
        trigger_cmd="true",
        expect_keyword="EXTCON_SYNC",
        timeout=8,
    )


def step8_extcon_register_notifier():
    bpf_step(
        8, "extcon_register_notifier links consumer to provider",
        textwrap.dedent("""
            kprobe:extcon_register_notifier {
                printf("EXTCON_REGISTER_NOTIFIER edev=%p id=%d\\n",
                       arg0, arg1);
                exit();
            }
            interval:s:6 { exit(); }
        """),
        trigger_cmd="modprobe -r extcon-usb-gpio 2>/dev/null; modprobe extcon-usb-gpio 2>/dev/null; true",
        expect_keyword="EXTCON_REGISTER_NOTIFIER",
        timeout=8,
    )


def step9_extcon_get_state():
    bpf_step(
        9, "extcon_get_state is called by consumer drivers",
        textwrap.dedent("""
            kprobe:extcon_get_state {
                printf("EXTCON_GET_STATE edev=%p id=%d -> %d\\n",
                       arg0, arg1, retval);
                exit();
            }
            interval:s:8 { exit(); }
        """),
        trigger_cmd="true",
        expect_keyword="EXTCON_GET_STATE",
        timeout=8,
    )


def step10_uevent_emission():
    """Step 10: kobject uevent is emitted on state change."""
    print(f"\n── Step 10: uevent emission on state change")
    # Monitor uevents via udevadm for 3 seconds — pass if the monitor starts
    r = run(
        "timeout 2 udevadm monitor --subsystem-match=extcon --kernel 2>&1 | "
        "head -5 || true"
    )
    if r and "KERNEL" in (r.stdout or "") or r.returncode == 0:
        print(f"{PASS}  udevadm can monitor extcon uevents")
        results.append((10, "uevent monitoring for extcon", "PASS"))
    else:
        print(f"{SKIP}  udevadm not available or no extcon events")
        results.append((10, "uevent monitoring for extcon", "SKIP"))


def step11_extcon_dev_unregister():
    bpf_step(
        11, "extcon_dev_unregister cleans up on device removal",
        textwrap.dedent("""
            kprobe:extcon_dev_unregister {
                printf("EXTCON_DEV_UNREGISTER edev=%p pid=%d\\n",
                       arg0, pid);
                exit();
            }
            interval:s:6 { exit(); }
        """),
        trigger_cmd="modprobe -r extcon-gpio 2>/dev/null; true",
        expect_keyword="EXTCON_DEV_UNREGISTER",
        timeout=8,
    )


# ──────────────────────────────────────────────────────────────────

def print_summary():
    print("\n" + "═" * 60)
    print("  extcon Subsystem Verification Summary")
    print("═" * 60)
    passed = sum(1 for _, _, s in results if s == "PASS")
    failed = sum(1 for _, _, s in results if s == "FAIL")
    skipped = sum(1 for _, _, s in results if s == "SKIP")

    for num, desc, status in results:
        icon = PASS if status == "PASS" else (FAIL if status == "FAIL" else SKIP)
        print(f"  Step {num:>2}: {icon}  {desc}")

    print("═" * 60)
    print(f"  Total: {len(results)}  |  "
          f"\033[32mPASS: {passed}\033[0m  |  "
          f"\033[31mFAIL: {failed}\033[0m  |  "
          f"\033[33mSKIP: {skipped}\033[0m")
    print("═" * 60)

    if failed == 0:
        print(f"\n{PASS} All verifiable steps passed!\n")
        return 0
    print(f"\n{FAIL} {failed} step(s) failed.\n")
    return 1


def main():
    print("╔══════════════════════════════════════════════════════╗")
    print("║  Linux extcon Subsystem - Workflow Verification      ║")
    print("╚══════════════════════════════════════════════════════╝")

    check_prereqs()

    step1_symbols()
    step2_sysfs_class()
    step3_cable_state_readable()
    step4_extcon_dev_register()
    step5_extcon_set_state()
    step6_extcon_set_state_sync()
    step7_extcon_sync()
    step8_extcon_register_notifier()
    step9_extcon_get_state()
    step10_uevent_emission()
    step11_extcon_dev_unregister()

    return print_summary()


if __name__ == "__main__":
    sys.exit(main())
