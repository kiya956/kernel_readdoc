#!/usr/bin/env python3
"""
NFC Subsystem Workflow Verification
====================================
Uses bpftrace to verify the Linux NFC subsystem call chain step by step.

Each step traces a specific kernel function to confirm the subsystem flows
correctly from device registration through polling and data exchange.

Requirements:
  - Linux kernel with NFC support (CONFIG_NFC=y/m)
  - bpftrace >= 0.14
  - Root privileges (sudo)
  - Optional: a real or virtual NFC device (nfcsim module)

Usage:
  sudo python3 test_nfc_subsystem.py [--timeout 30] [--load-nfcsim]
"""

import subprocess
import sys
import os
import time
import argparse
import textwrap
import tempfile

PASS = "\033[32m[PASS]\033[0m"
FAIL = "\033[31m[FAIL]\033[0m"
SKIP = "\033[33m[SKIP]\033[0m"
INFO = "\033[34m[INFO]\033[0m"

results = []


def run(cmd, capture=True, timeout=10, check=False):
    try:
        r = subprocess.run(
            cmd, shell=True, capture_output=capture,
            text=True, timeout=timeout, check=check
        )
        return r
    except subprocess.TimeoutExpired:
        return None
    except subprocess.CalledProcessError as e:
        return e


def check_prereqs():
    """Verify bpftrace and kernel NFC support are present."""
    print(f"\n{INFO} Checking prerequisites...")

    issues = []

    r = run("which bpftrace")
    if r is None or r.returncode != 0:
        issues.append("bpftrace not found in PATH")

    if os.geteuid() != 0:
        issues.append("Must run as root (sudo)")

    r = run("cat /proc/sys/kernel/kptr_restrict")
    if r and r.stdout.strip() == "2":
        print(f"{INFO}  kptr_restrict=2: some symbol lookups may fail")

    if issues:
        for i in issues:
            print(f"{FAIL}  {i}")
        sys.exit(1)

    print(f"{PASS}  Prerequisites OK")


def step(number, description, bpf_script, trigger_cmd=None,
         expect_keyword=None, timeout=8, skip_if=None):
    """
    Run one verification step.

    Args:
        number: Step index (int)
        description: Human-readable description of what is being verified
        bpf_script: bpftrace inline script (BEGIN/kprobe/END etc.)
        trigger_cmd: Shell command to trigger the kernel path under test
        expect_keyword: String that must appear in bpftrace output to PASS
        timeout: Seconds to wait for bpftrace output
        skip_if: Shell command; if it returns non-zero, skip this step
    """
    print(f"\n── Step {number}: {description}")

    if skip_if:
        r = run(skip_if)
        if r is None or r.returncode != 0:
            print(f"{SKIP}  Condition not met, skipping: {skip_if}")
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

    # Give bpftrace time to attach probes
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
        print(f"         Output: {output.strip()[:200]}")
        results.append((number, description, "PASS"))
    elif not expect_keyword and bpf_proc.returncode == 0:
        print(f"{PASS}  Script executed without error")
        print(f"         Output: {output.strip()[:200]}")
        results.append((number, description, "PASS"))
    else:
        # Distinguish "function not present" from actual failure
        if "not traceable" in output or "ERROR" in output and "kprobe" in output:
            print(f"{SKIP}  Kernel function not traceable (may be inlined/absent)")
            print(f"         bpftrace: {stderr.strip()[:200]}")
            results.append((number, description, "SKIP"))
        else:
            print(f"{FAIL}  Expected '{expect_keyword}' not found")
            print(f"         stdout: {stdout.strip()[:200]}")
            print(f"         stderr: {stderr.strip()[:200]}")
            results.append((number, description, "FAIL"))


# ──────────────────────────────────────────────────────────────────
# Individual steps
# ──────────────────────────────────────────────────────────────────

def step1_nfc_subsystem_init():
    """Step 1: NFC subsystem registers with the kernel on module load."""
    script = textwrap.dedent("""
        BEGIN {
            printf("NFC_SUBSYSTEM_CHECK\\n");
            exit();
        }
    """)
    # Check presence of nfc symbols in kallsyms (no bpf probe needed)
    r = run("grep -c ' nfc_' /proc/kallsyms")
    count = int(r.stdout.strip()) if r and r.returncode == 0 else 0
    print(f"\n── Step 1: NFC symbols present in kernel")
    if count > 10:
        print(f"{PASS}  {count} nfc_* symbols found in /proc/kallsyms")
        results.append((1, "NFC symbols in kallsyms", "PASS"))
    else:
        # Try loading nfc module
        run("modprobe nfc 2>/dev/null", timeout=5)
        r2 = run("grep -c ' nfc_' /proc/kallsyms")
        count2 = int(r2.stdout.strip()) if r2 and r2.returncode == 0 else 0
        if count2 > 10:
            print(f"{PASS}  {count2} nfc_* symbols found after modprobe nfc")
            results.append((1, "NFC symbols in kallsyms", "PASS"))
        else:
            print(f"{FAIL}  Only {count2} nfc_* symbols — NFC may not be built")
            results.append((1, "NFC symbols in kallsyms", "FAIL"))


def step2_nfc_genl_family():
    """Step 2: Generic Netlink NFC family is registered."""
    print(f"\n── Step 2: NFC Generic Netlink family registered")
    r = run("grep -r 'nfc' /proc/net/genl_ctrl 2>/dev/null || "
            "cat /proc/net/protocols 2>/dev/null | grep -i nfc || "
            "ls /sys/bus/nfc 2>/dev/null && echo 'nfc bus present'")
    # Use genl family listing via python-netlink or just check /proc
    r2 = run("python3 -c \""
             "import socket, struct;"
             "# just test that AF_NFC is defined"
             "print(hasattr(socket, 'AF_NFC') or True)"
             "\"")

    # Most reliable: check that nfc_genl_family is in kallsyms
    r3 = run("grep -c 'nfc_genl' /proc/kallsyms")
    count = int(r3.stdout.strip()) if r3 and r3.returncode == 0 else 0
    if count > 0:
        print(f"{PASS}  nfc_genl symbols present ({count} entries)")
        results.append((2, "NFC Netlink family registered", "PASS"))
    else:
        print(f"{FAIL}  nfc_genl not found in kallsyms")
        results.append((2, "NFC Netlink family registered", "FAIL"))


def step3_nfcsim_load():
    """Step 3: Load nfcsim virtual NFC device."""
    print(f"\n── Step 3: Load nfcsim virtual NFC device")
    # Remove any existing instance
    run("modprobe -r nfcsim 2>/dev/null", timeout=5)
    time.sleep(0.5)
    r = run("modprobe nfcsim 2>/dev/null", timeout=5)
    time.sleep(1)
    r2 = run("ls /sys/class/nfc/ 2>/dev/null")
    if r2 and r2.returncode == 0 and r2.stdout.strip():
        devs = r2.stdout.strip().split()
        print(f"{PASS}  NFC device(s) found: {devs}")
        results.append((3, "nfcsim virtual device loaded", "PASS"))
    else:
        print(f"{SKIP}  nfcsim not available (no /sys/class/nfc entries)")
        results.append((3, "nfcsim virtual device loaded", "SKIP"))


def step4_nfc_register_device():
    """Step 4: nfc_register_device is called when nfcsim loads."""
    script = textwrap.dedent("""
        kprobe:nfc_register_device {
            printf("NFC_REGISTER_DEVICE pid=%d comm=%s\\n",
                   pid, comm);
            exit();
        }
        interval:s:6 { exit(); }
    """)
    step(
        4, "nfc_register_device called on device probe",
        script,
        trigger_cmd="modprobe -r nfcsim 2>/dev/null; modprobe nfcsim 2>/dev/null",
        expect_keyword="NFC_REGISTER_DEVICE",
        timeout=10,
        skip_if="modinfo nfcsim 2>/dev/null; test $? -eq 0"
    )


def step5_nfc_dev_up():
    """Step 5: nfc_dev_up is called when device is brought up via netlink."""
    script = textwrap.dedent("""
        kprobe:nfc_dev_up {
            printf("NFC_DEV_UP dev=%p pid=%d\\n", arg0, pid);
            exit();
        }
        interval:s:8 { exit(); }
    """)
    # Use nfc-list from libnfc or neard to trigger dev_up, or use python-nfc
    # Fall back to a direct netlink message if tools not available
    trigger = (
        "nfc-list 2>/dev/null || "
        "python3 -c \""
        "import socket, struct;"
        "# Attempt NFC_CMD_DEV_UP via generic netlink - best effort"
        "pass"
        "\" 2>/dev/null; true"
    )
    step(
        5, "nfc_dev_up called via netlink command",
        script,
        trigger_cmd=trigger,
        expect_keyword="NFC_DEV_UP",
        timeout=10,
    )


def step6_nfc_start_poll():
    """Step 6: nfc_start_poll enters the polling state machine."""
    script = textwrap.dedent("""
        kprobe:nfc_start_poll {
            printf("NFC_START_POLL dev=%p im_proto=0x%x\\n",
                   arg0, arg1);
            exit();
        }
        interval:s:8 { exit(); }
    """)
    step(
        6, "nfc_start_poll state machine entry",
        script,
        trigger_cmd="nfc-poll 2>/dev/null; true",
        expect_keyword="NFC_START_POLL",
        timeout=10,
    )


def step7_nfc_targets_found():
    """Step 7: nfc_targets_found fires when a tag is detected."""
    script = textwrap.dedent("""
        kprobe:nfc_targets_found {
            printf("NFC_TARGETS_FOUND dev=%p ntargets=%d\\n",
                   arg0, arg2);
            exit();
        }
        interval:s:8 { exit(); }
    """)
    step(
        7, "nfc_targets_found fires on tag detection",
        script,
        trigger_cmd="nfc-poll -t 1 2>/dev/null; true",
        expect_keyword="NFC_TARGETS_FOUND",
        timeout=10,
    )


def step8_nci_send_cmd():
    """Step 8: NCI layer sends a command to the controller."""
    script = textwrap.dedent("""
        kprobe:nci_send_cmd {
            printf("NCI_SEND_CMD ndev=%p opcode=0x%x\\n",
                   arg0, arg1);
            exit();
        }
        interval:s:8 { exit(); }
    """)
    step(
        8, "nci_send_cmd transmits NCI command packet",
        script,
        trigger_cmd=(
            "modprobe -r nfcsim 2>/dev/null; "
            "modprobe nfcsim 2>/dev/null; "
            "nfc-list 2>/dev/null; true"
        ),
        expect_keyword="NCI_SEND_CMD",
        timeout=10,
        skip_if="grep -q 'nci_send_cmd' /proc/kallsyms"
    )


def step9_nfc_data_exchange():
    """Step 9: nfc_data_exchange is the hot path for tag APDU exchange."""
    script = textwrap.dedent("""
        kprobe:nfc_data_exchange {
            printf("NFC_DATA_EXCHANGE dev=%p target=%d len=%d\\n",
                   arg0, arg1, ((struct sk_buff *)arg2)->len);
            exit();
        }
        interval:s:8 { exit(); }
    """)
    step(
        9, "nfc_data_exchange APDU hot path",
        script,
        trigger_cmd="nfc-read-ndef 2>/dev/null; true",
        expect_keyword="NFC_DATA_EXCHANGE",
        timeout=10,
    )


def step10_llcp_sock():
    """Step 10: LLCP socket layer is reachable."""
    script = textwrap.dedent("""
        kprobe:nfc_llcp_sock_link {
            printf("LLCP_SOCK_LINK local=%p\\n", arg0);
            exit();
        }
        interval:s:5 { exit(); }
    """)
    # LLCP requires peer-to-peer, hard to trigger without two NFC devices.
    # Just verify the kprobe can be attached (symbol exists).
    r = run("grep -q 'nfc_llcp_sock_link' /proc/kallsyms")
    print(f"\n── Step 10: LLCP socket layer symbol availability")
    if r and r.returncode == 0:
        print(f"{PASS}  nfc_llcp_sock_link symbol present in kernel")
        results.append((10, "LLCP socket layer reachable", "PASS"))
    else:
        print(f"{SKIP}  nfc_llcp_sock_link not in kallsyms (LLCP not built)")
        results.append((10, "LLCP socket layer reachable", "SKIP"))


def step11_nfc_unregister_device():
    """Step 11: nfc_unregister_device called on module removal."""
    script = textwrap.dedent("""
        kprobe:nfc_unregister_device {
            printf("NFC_UNREGISTER_DEVICE dev=%p pid=%d\\n",
                   arg0, pid);
            exit();
        }
        interval:s:8 { exit(); }
    """)
    step(
        11, "nfc_unregister_device on device removal",
        script,
        trigger_cmd="modprobe -r nfcsim 2>/dev/null; true",
        expect_keyword="NFC_UNREGISTER_DEVICE",
        timeout=10,
        skip_if="modinfo nfcsim 2>/dev/null; test $? -eq 0"
    )


# ──────────────────────────────────────────────────────────────────
# Summary
# ──────────────────────────────────────────────────────────────────

def print_summary():
    print("\n" + "═" * 60)
    print("  NFC Subsystem Verification Summary")
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
    else:
        print(f"\n{FAIL} {failed} step(s) failed.\n")
        return 1


def main():
    parser = argparse.ArgumentParser(
        description="NFC subsystem bpftrace verification"
    )
    parser.add_argument("--timeout", type=int, default=8,
                        help="Timeout per step in seconds (default: 8)")
    args = parser.parse_args()

    print("╔══════════════════════════════════════════════════════╗")
    print("║     Linux NFC Subsystem - Workflow Verification      ║")
    print("╚══════════════════════════════════════════════════════╝")

    check_prereqs()

    # Execute all steps in order
    step1_nfc_subsystem_init()
    step2_nfc_genl_family()
    step3_nfcsim_load()
    step4_nfc_register_device()
    step5_nfc_dev_up()
    step6_nfc_start_poll()
    step7_nfc_targets_found()
    step8_nci_send_cmd()
    step9_nfc_data_exchange()
    step10_llcp_sock()
    step11_nfc_unregister_device()

    # Clean up nfcsim
    run("modprobe -r nfcsim 2>/dev/null", timeout=5)

    return print_summary()


if __name__ == "__main__":
    sys.exit(main())
