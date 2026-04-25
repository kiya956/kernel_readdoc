#!/usr/bin/env python3
"""
test_rfkill_subsystem.py — bpftrace-based verification of the rfkill subsystem.

Steps
-----
1.  Probe rfkill_register              — rfkill device registration
2.  Probe rfkill_alloc                 — rfkill object allocation
3.  Probe rfkill_set_sw_state          — soft block state change
4.  Probe rfkill_set_hw_state          — hard block state change
5.  Probe rfkill_init_sw_state         — initial soft state setup
6.  Probe rfkill_fop_open              — /dev/rfkill open
7.  Probe rfkill_fop_read              — /dev/rfkill read
8.  Probe rfkill_fop_poll              — /dev/rfkill poll
9.  Probe rfkill_send_events           — broadcast rfkill events
10. Check /sys/class/rfkill presence   — sysfs round-trip
"""

import subprocess
import sys
import os
import time
import tempfile

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
SKIP = "\033[33mSKIP\033[0m"

BPFTRACE = "/usr/bin/bpftrace"
ATTACH_WAIT = 2.0


def run_bpftrace(program: str, trigger=None, timeout: int = 10) -> tuple[str, str, bool]:
    with tempfile.NamedTemporaryFile("w", suffix=".bt", delete=False) as f:
        f.write(program)
        bt_file = f.name
    try:
        proc = subprocess.Popen(
            [BPFTRACE, bt_file],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        time.sleep(ATTACH_WAIT)
        if trigger:
            try:
                trigger()
            except Exception:
                pass
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
        skipped = any(
            kw in stderr for kw in ("not traceable", "No probes", "unrecognized")
        )
        return stdout, stderr, skipped
    finally:
        os.unlink(bt_file)


results = []


def check(step_num: int, name: str, program: str, trigger=None,
          expect: str = "HIT", timeout: int = 12):
    stdout, stderr, skipped = run_bpftrace(program, trigger, timeout)
    if skipped:
        results.append((step_num, name, SKIP))
        print(f"  Step {step_num:2d}: {name:50s} {SKIP}")
        return
    ok = expect in stdout
    status = PASS if ok else FAIL
    results.append((step_num, name, status))
    print(f"  Step {step_num:2d}: {name:50s} {status}")
    if not ok:
        if stdout.strip():
            print(f"            stdout: {stdout.strip()[:200]}")
        if stderr.strip():
            print(f"            stderr: {stderr.strip()[:200]}")


print("\n=== rfkill subsystem bpftrace verification ===\n")

# ── helpers ──────────────────────────────────────────────────────────────────

def trigger_rfkill_list():
    """Run 'rfkill list' to exercise the /dev/rfkill read path."""
    subprocess.run(["rfkill", "list"], capture_output=True, timeout=5)


def trigger_dev_rfkill_open_read():
    """Open and read /dev/rfkill directly."""
    try:
        fd = os.open("/dev/rfkill", os.O_RDONLY | os.O_NONBLOCK)
        try:
            os.read(fd, 64)
        except OSError:
            pass
        os.close(fd)
    except OSError:
        pass


def trigger_sysfs_read():
    """Read rfkill state through sysfs."""
    subprocess.run(
        ["cat", "/sys/class/rfkill/rfkill0/state"],
        capture_output=True, timeout=5,
    )


# ── Step 1: rfkill_register ─────────────────────────────────────────────────

check(1, "rfkill_register probe", r"""
kprobe:rfkill_register
{
    printf("HIT rfkill_register\n");
    exit();
}

interval:s:5 { exit(); }
""", trigger=trigger_rfkill_list)

# ── Step 2: rfkill_alloc ────────────────────────────────────────────────────

check(2, "rfkill_alloc probe", r"""
kprobe:rfkill_alloc
{
    printf("HIT rfkill_alloc\n");
    exit();
}

interval:s:5 { exit(); }
""", trigger=trigger_rfkill_list)

# ── Step 3: rfkill_set_sw_state ─────────────────────────────────────────────

check(3, "rfkill_set_sw_state probe", r"""
kprobe:rfkill_set_sw_state
{
    printf("HIT rfkill_set_sw_state\n");
    exit();
}

interval:s:5 { exit(); }
""", trigger=trigger_rfkill_list)

# ── Step 4: rfkill_set_hw_state ─────────────────────────────────────────────

check(4, "rfkill_set_hw_state probe", r"""
kprobe:rfkill_set_hw_state
{
    printf("HIT rfkill_set_hw_state\n");
    exit();
}

interval:s:5 { exit(); }
""", trigger=trigger_rfkill_list)

# ── Step 5: rfkill_init_sw_state ────────────────────────────────────────────

check(5, "rfkill_init_sw_state probe", r"""
kprobe:rfkill_init_sw_state
{
    printf("HIT rfkill_init_sw_state\n");
    exit();
}

interval:s:5 { exit(); }
""", trigger=trigger_rfkill_list)

# ── Step 6: rfkill_fop_open ─────────────────────────────────────────────────

check(6, "rfkill_fop_open probe", r"""
kprobe:rfkill_fop_open
{
    printf("HIT rfkill_fop_open\n");
    exit();
}

interval:s:5 { exit(); }
""", trigger=trigger_dev_rfkill_open_read)

# ── Step 7: rfkill_fop_read ─────────────────────────────────────────────────

check(7, "rfkill_fop_read probe", r"""
kprobe:rfkill_fop_read
{
    printf("HIT rfkill_fop_read\n");
    exit();
}

interval:s:5 { exit(); }
""", trigger=trigger_dev_rfkill_open_read)

# ── Step 8: rfkill_fop_poll ─────────────────────────────────────────────────

check(8, "rfkill_fop_poll probe", r"""
kprobe:rfkill_fop_poll
{
    printf("HIT rfkill_fop_poll\n");
    exit();
}

interval:s:5 { exit(); }
""", trigger=trigger_dev_rfkill_open_read)

# ── Step 9: rfkill_send_events ──────────────────────────────────────────────

check(9, "rfkill_send_events probe", r"""
kprobe:rfkill_send_events
{
    printf("HIT rfkill_send_events\n");
    exit();
}

interval:s:5 { exit(); }
""", trigger=trigger_rfkill_list)

# ── Step 10: /sys/class/rfkill sysfs presence ───────────────────────────────

RFKILL_SYSFS = "/sys/class/rfkill"

if os.path.isdir(RFKILL_SYSFS):
    entries = os.listdir(RFKILL_SYSFS)
    if entries:
        detail = ", ".join(sorted(entries)[:8])
        results.append((10, "sysfs /sys/class/rfkill presence", PASS))
        print(f"  Step 10: {'sysfs /sys/class/rfkill presence':50s} {PASS}")
        print(f"            devices: {detail}")
    else:
        results.append((10, "sysfs /sys/class/rfkill presence", FAIL))
        print(f"  Step 10: {'sysfs /sys/class/rfkill presence':50s} {FAIL}")
        print("            directory exists but is empty — no rfkill devices")
else:
    results.append((10, "sysfs /sys/class/rfkill presence", FAIL))
    print(f"  Step 10: {'sysfs /sys/class/rfkill presence':50s} {FAIL}")
    print("            /sys/class/rfkill directory not found")

# ── Summary ──────────────────────────────────────────────────────────────────

print()
passed = sum(1 for _, _, s in results if s == PASS)
failed = sum(1 for _, _, s in results if s == FAIL)
skipped = sum(1 for _, _, s in results if s == SKIP)
total = len(results)
print(f"Results: {passed}/{total} passed, {failed} failed, {skipped} skipped")
if failed > 0:
    sys.exit(1)
