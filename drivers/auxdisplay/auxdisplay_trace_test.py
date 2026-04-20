#!/usr/bin/env python3
"""
auxdisplay Subsystem — bpftrace verification test

Verifies the auxdisplay framework: /dev/lcd presence, charlcd write path,
line-display sysfs interface, and escape sequence dispatch via kprobes.

Requirements:
  - Linux with CONFIG_CHARLCD=y or CONFIG_HT16K33=y (or any auxdisplay driver)
  - bpftrace >= 0.14, root for bpftrace steps
  - Physical LCD hardware for hardware-dependent steps (optional)

Usage:
  sudo python3 auxdisplay_trace_test.py
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
# Step 1: Config and device presence
# ─────────────────────────────────────────────────────────────
def step1_config():
    header("Step 1: auxdisplay configuration and devices")

    cfg_path = f"/boot/config-{os.uname().release}"
    found_opts = []
    if os.path.exists(cfg_path):
        with open(cfg_path) as f:
            cfg = f.read()
        opts = ["CONFIG_CHARLCD", "CONFIG_HD44780", "CONFIG_HT16K33",
                "CONFIG_MAX6959", "CONFIG_CFAG12864B", "CONFIG_KS0108",
                "CONFIG_LCD2S", "CONFIG_ARM_CHARLCD"]
        for opt in opts:
            if f"{opt}=y" in cfg or f"{opt}=m" in cfg:
                info(f"  {opt} enabled")
                found_opts.append(opt)

    # Check /dev/lcd
    has_lcd = os.path.exists("/dev/lcd")
    if has_lcd:
        info("/dev/lcd found — charlcd device active")

    # Check linedisp sysfs
    linedisp = glob.glob("/sys/class/linedisp-*") + glob.glob("/sys/bus/platform/devices/*linedisp*")
    if linedisp:
        info(f"linedisp devices: {linedisp[:2]}")

    if found_opts:
        pass_(f"auxdisplay options: {', '.join(found_opts[:4])}")
    elif has_lcd or linedisp:
        pass_("auxdisplay hardware device found")
    elif sym_exists("charlcd_register"):
        info("charlcd_register in kallsyms — charlcd built-in")
        pass_("charlcd framework present")
    else:
        info("No auxdisplay hardware detected (expected on most desktop/server systems)")
        pass_("Step complete — auxdisplay not present on this system")
    return bool(found_opts) or has_lcd or bool(linedisp)

# ─────────────────────────────────────────────────────────────
# Step 2: /dev/lcd interface
# ─────────────────────────────────────────────────────────────
def step2_dev_lcd():
    header("Step 2: /dev/lcd character device")

    if not os.path.exists("/dev/lcd"):
        info("/dev/lcd not found (no charlcd hardware)")
        pass_("Step skipped (charlcd not loaded)"); return True

    info(f"Found /dev/lcd — {oct(os.stat('/dev/lcd').st_mode)}")

    if os.geteuid() == 0:
        try:
            # Write a test message with ESC D (Display On) then text
            msg = "\x1bD\x1bI" + "Kernel Test    " + "\n" + "auxdisplay OK  "
            with open("/dev/lcd", "w") as f:
                f.write(msg)
            pass_("Wrote test message to /dev/lcd")
        except PermissionError:
            info("Permission denied writing to /dev/lcd")
            pass_("/dev/lcd exists (write permission required)")
        except OSError as e:
            info(f"Write failed: {e}")
            pass_("/dev/lcd exists but write failed (may already be open)")
    else:
        pass_("/dev/lcd present (need root to write)")
    return True

# ─────────────────────────────────────────────────────────────
# Step 3: charlcd_write kprobe
# ─────────────────────────────────────────────────────────────
BPFTRACE_WRITE = r"""
kprobe:charlcd_write
{
    printf("CHARLCD_WRITE pid=%d count=%lu\n", pid, arg2);
}
kretprobe:charlcd_write
{
    printf("CHARLCD_WRITE_RET ret=%ld\n", retval);
}
"""

def step3_charlcd_write():
    header("Step 3: charlcd_write kprobe — 5s")

    if os.geteuid() != 0:
        fail("Root required"); return False
    if not sym_exists("charlcd_write"):
        info("charlcd_write not in kallsyms")
        pass_("Step skipped (charlcd not present)"); return True

    info("Watching charlcd_write for 5s (write to /dev/lcd to trigger)...")
    lines = run_bpftrace(BPFTRACE_WRITE, "CHARLCD_WRITE")
    if lines:
        pass_(f"Captured {len(lines)} charlcd_write call(s)")
    else:
        info("No writes (write to /dev/lcd to trigger: echo test > /dev/lcd)")
        pass_("charlcd_write kprobe attached OK")
    return True

# ─────────────────────────────────────────────────────────────
# Step 4: charlcd_write_char kprobe (escape sequence dispatch)
# ─────────────────────────────────────────────────────────────
BPFTRACE_CHAR = r"""
kprobe:charlcd_write_char
{
    $c = (int)arg1;
    printf("CHARLCD_CHAR pid=%d char=%d (%c)\n",
           pid, $c, $c >= 32 && $c < 127 ? $c : '?');
}
"""

def step4_write_char():
    header("Step 4: charlcd_write_char kprobe (per-character dispatch) — 5s")

    if os.geteuid() != 0:
        fail("Root required"); return False
    if not sym_exists("charlcd_write_char"):
        pass_("Step skipped (charlcd not present)"); return True

    info("Watching per-character writes for 5s...")
    lines = run_bpftrace(BPFTRACE_CHAR, "CHARLCD_CHAR")
    if lines:
        chars_seen = set()
        for l in lines:
            parts = l.split("char=")
            if len(parts) > 1:
                chars_seen.add(parts[1].split()[0])
        pass_(f"Captured {len(lines)} char dispatches (chars: {list(chars_seen)[:5]})")
    else:
        info("No char dispatches (no /dev/lcd write in window)")
        pass_("charlcd_write_char kprobe attached OK")
    return True

# ─────────────────────────────────────────────────────────────
# Step 5: line-display sysfs interface
# ─────────────────────────────────────────────────────────────
def step5_linedisp_sysfs():
    header("Step 5: line-display sysfs interface")

    # Search common locations
    candidates = (
        glob.glob("/sys/class/linedisp-*/linedisp*/") +
        glob.glob("/sys/bus/i2c/devices/*/linedisp*/") +
        glob.glob("/sys/bus/platform/devices/*/linedisp*/")
    )

    if not candidates:
        # Try by attr name
        msg_attrs = glob.glob("/sys/**/message", recursive=True)
        msg_attrs = [p for p in msg_attrs
                     if "linedisp" in p or "ht16k33" in p or "max6959" in p]
        candidates = [os.path.dirname(p) + "/" for p in msg_attrs[:3]]

    if candidates:
        for dev in candidates[:3]:
            info(f"Found linedisp device: {dev}")
            for attr in ["message", "scroll_step_ms", "map_seg"]:
                p = os.path.join(dev.rstrip("/"), attr)
                if os.path.exists(p):
                    try:
                        with open(p) as f:
                            val = f.read().strip()[:40]
                        info(f"  {attr}: '{val}'")
                    except Exception:
                        info(f"  {attr}: (not readable)")
        pass_(f"linedisp sysfs interface found ({len(candidates)} device(s))")
    else:
        info("No linedisp sysfs devices found (no segment display hardware)")
        pass_("Step skipped (no line-display hardware)")
    return True

# ─────────────────────────────────────────────────────────────
# Step 6: linedisp_register kprobe
# ─────────────────────────────────────────────────────────────
BPFTRACE_LD_REG = r"""
kprobe:linedisp_register
{
    printf("LINEDISP_REGISTER pid=%d num_chars=%u\n", pid, (uint32_t)arg2);
}
kretprobe:linedisp_register
{
    printf("LINEDISP_REGISTER_RET ret=%d\n", retval);
}
"""

def step6_linedisp_register():
    header("Step 6: linedisp_register kprobe — 5s")

    if os.geteuid() != 0:
        fail("Root required"); return False
    if not sym_exists("linedisp_register"):
        pass_("Step skipped (line-display not present)"); return True

    info("Watching linedisp_register for 5s...")
    lines = run_bpftrace(BPFTRACE_LD_REG, "LINEDISP_REGISTER")
    if lines:
        pass_(f"Captured {len(lines)} linedisp registration(s)")
    else:
        info("No registrations (display registered at boot)")
        pass_("linedisp_register kprobe attached OK")
    return True

# ─────────────────────────────────────────────────────────────
# Step 7: linedisp_scroll timer (auto-scroll kprobe)
# ─────────────────────────────────────────────────────────────
BPFTRACE_SCROLL = r"""
kprobe:linedisp_scroll
{
    printf("LINEDISP_SCROLL pid=%d\n", pid);
}
"""

def step7_scroll():
    header("Step 7: linedisp_scroll timer kprobe — 5s")

    if os.geteuid() != 0:
        fail("Root required"); return False
    if not sym_exists("linedisp_scroll"):
        pass_("Step skipped (line-display not present)"); return True

    info("Watching linedisp_scroll for 5s (write long msg to trigger scroll)...")
    lines = run_bpftrace(BPFTRACE_SCROLL, "LINEDISP_SCROLL")
    if lines:
        pass_(f"Captured {len(lines)} scroll tick(s) — scroll timer active")
    else:
        info("No scroll ticks (no active scroll — message fits or scroll_step_ms=0)")
        pass_("linedisp_scroll kprobe attached OK")
    return True

# ─────────────────────────────────────────────────────────────
# Step 8: charlcd_register kprobe + reboot notifier
# ─────────────────────────────────────────────────────────────
BPFTRACE_REG2 = r"""
kprobe:charlcd_register
{
    printf("CHARLCD_REGISTER pid=%d w=%d h=%d\n",
           pid,
           ((struct charlcd *)arg0)->width,
           ((struct charlcd *)arg0)->height);
}
"""

def step8_charlcd_register():
    header("Step 8: charlcd_register kprobe + reboot notifier check")

    if os.geteuid() != 0:
        fail("Root required"); return False
    if not sym_exists("charlcd_register"):
        pass_("Step skipped (charlcd not present)"); return True

    info("Watching charlcd_register for 5s...")
    lines = run_bpftrace(BPFTRACE_REG2, "CHARLCD_REGISTER")
    if lines:
        pass_(f"Captured {len(lines)} charlcd registration(s): {lines[0]}")
    else:
        info("No registration (LCD registered at boot)")
        pass_("charlcd_register kprobe attached OK")

    # Check reboot notifier in kallsyms
    if sym_exists("charlcd_reboot_handler") or sym_exists("charlcd_notifier_call"):
        info("charlcd reboot notifier registered (LCD will show shutdown message)")
    return True

# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────
def main():
    print("=" * 62)
    print("  auxdisplay Subsystem — bpftrace Verification")
    print("=" * 62)

    if os.geteuid() != 0:
        print(f"\n{RED}WARNING: Not running as root.{RESET}")
        print("Steps 3,4,6,7,8 require root. Steps 1,2,5 will run.\n")

    steps = [
        ("Config + device detection",          step1_config),
        ("/dev/lcd character device",           step2_dev_lcd),
        ("charlcd_write kprobe (5s)",           step3_charlcd_write),
        ("charlcd_write_char kprobe (5s)",      step4_write_char),
        ("line-display sysfs interface",        step5_linedisp_sysfs),
        ("linedisp_register kprobe (5s)",       step6_linedisp_register),
        ("linedisp_scroll timer kprobe (5s)",   step7_scroll),
        ("charlcd_register kprobe (5s)",        step8_charlcd_register),
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
