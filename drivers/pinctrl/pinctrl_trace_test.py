#!/usr/bin/env python3
"""Pin controller subsystem verification via debugfs and bpftrace."""
import subprocess, os, sys, glob, tempfile, re

PASS="\033[32mPASS\033[0m"; FAIL="\033[31mFAIL\033[0m"
results=[]

def record(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))

def check_root(): return os.geteuid() == 0
def bpftrace_available():
    try: return subprocess.run(["bpftrace","--version"], capture_output=True, timeout=5).returncode == 0
    except: return False

def symbol_exists(sym):
    try:
        r = subprocess.run(["grep","-wc",sym,"/proc/kallsyms"], capture_output=True, text=True, timeout=5)
        return r.returncode == 0 and int(r.stdout.strip()) > 0
    except: return False

def run_bpftrace(script, timeout=9):
    with tempfile.NamedTemporaryFile("w", suffix=".bt", delete=False) as f:
        f.write(script); fname = f.name
    try:
        r = subprocess.run(["bpftrace", fname], capture_output=True, text=True, timeout=timeout)
        return r.stdout + r.stderr
    except subprocess.TimeoutExpired: return ""
    finally: os.unlink(fname)

def sysfs_read(p):
    try:
        with open(p) as f: return f.read().strip()
    except: return None

print("=" * 64)
print("  pinctrl Subsystem Verification  (drivers/pinctrl/)")
print("=" * 64)

# ── Step 1: Prerequisites ────────────────────────────────────────────
print("\n── Step 1: Prerequisites ──────────────────────────────────────")
record("running as root", check_root())
record("bpftrace available", bpftrace_available())
record("pinctrl_register symbol", symbol_exists("pinctrl_register"))
record("pinctrl_select_state symbol", symbol_exists("pinctrl_select_state"))

# ── Step 2: debugfs pinctrl controllers ─────────────────────────────
print("\n── Step 2: debugfs pinctrl controllers ───────────────────────")
pctrl_dirs = [d for d in glob.glob("/sys/kernel/debug/pinctrl/*/")
              if os.path.basename(d.rstrip('/')) != "pinctrl-maps"]
record("pinctrl controller debugfs dirs", len(pctrl_dirs) > 0,
       f"{[os.path.basename(d.rstrip('/')) for d in pctrl_dirs[:4]]}")
for d in pctrl_dirs[:2]:
    n = os.path.basename(d.rstrip('/'))
    for f in ["pins", "groups", "functions", "pinmux-pins"]:
        p = f"{d}{f}"
        if os.path.exists(p):
            v = sysfs_read(p)
            lines = v.split('\n') if v else []
            record(f"  {n}/{f}", v is not None, f"{len(lines)} lines")

# ── Step 3: pinctrl-maps debugfs ────────────────────────────────────
print("\n── Step 3: pinctrl-maps debugfs ──────────────────────────────")
maps_path = "/sys/kernel/debug/pinctrl/pinctrl-maps"
if os.path.exists(maps_path):
    content = sysfs_read(maps_path) or ""
    lines = content.split('\n')
    record("pinctrl-maps readable", bool(content), f"{len(lines)} lines")
    mux_count = sum(1 for l in lines if "type: MUX_GROUP" in l)
    conf_count = sum(1 for l in lines if "type: CONFIGS" in l)
    record("mux mappings present", mux_count > 0, f"{mux_count} mux, {conf_count} conf")
else:
    record("pinctrl-maps", False, "debugfs not mounted")

# ── Step 4: kprobe pinctrl_select_state ─────────────────────────────
print("\n── Step 4: kprobe pinctrl_select_state ───────────────────────")
if bpftrace_available() and symbol_exists("pinctrl_select_state"):
    script = """
kprobe:pinctrl_select_state { @sel[comm] = count(); }
interval:s:5 { print(@sel); printf("SEL_DONE\\n"); exit(); }
"""
    out = run_bpftrace(script, timeout=9)
    done = "SEL_DONE" in out
    record("pinctrl_select_state kprobe compiles+runs", done)
    fired = bool(re.search(r':\s*[1-9]', out))
    record("pinctrl_select_state fired in 5s", fired,
           "no state changes in window" if not fired else "")
else:
    record("pinctrl_select_state kprobe", False, "bpftrace or symbol missing")

# ── Step 5: pinctrl_select_state latency ────────────────────────────
print("\n── Step 5: pinctrl_select_state latency histogram ────────────")
if bpftrace_available() and symbol_exists("pinctrl_select_state"):
    script = """
kprobe:pinctrl_select_state    { @s[tid] = nsecs; }
kretprobe:pinctrl_select_state {
    if (@s[tid]) { @lat_us = hist((nsecs-@s[tid])/1000); delete(@s[tid]); }
}
interval:s:5 { print(@lat_us); printf("LAT_DONE\\n"); exit(); }
"""
    out = run_bpftrace(script, timeout=9)
    record("pinctrl_select_state latency kprobe ran", "LAT_DONE" in out)
else:
    record("pinctrl_select_state latency", False, "bpftrace or symbol missing")

# ── Step 6: pinctrl_bind_pins kprobe ────────────────────────────────
print("\n── Step 6: kprobe pinctrl_bind_pins ──────────────────────────")
if bpftrace_available() and symbol_exists("pinctrl_bind_pins"):
    script = """
kprobe:pinctrl_bind_pins { @binds[comm] = count(); }
interval:s:4 { print(@binds); printf("BIND_DONE\\n"); exit(); }
"""
    out = run_bpftrace(script, timeout=7)
    record("pinctrl_bind_pins kprobe compiles", "BIND_DONE" in out)
else:
    record("pinctrl_bind_pins kprobe", False, "bpftrace or symbol missing")

# ── Step 7: GPIO-pinctrl integration symbols ─────────────────────────
print("\n── Step 7: GPIO-pinctrl integration symbols ──────────────────")
for sym, desc in [("pinctrl_gpio_request", "GPIO→pinctrl request"),
                  ("pinctrl_gpio_free", "GPIO→pinctrl free"),
                  ("pinctrl_gpio_direction_input", "GPIO direction input"),
                  ("pinctrl_gpio_direction_output", "GPIO direction output")]:
    record(f"{desc} ({sym})", symbol_exists(sym))

# ── Step 8: pinmux ops symbols ───────────────────────────────────────
print("\n── Step 8: pinmux / pinconf symbols ──────────────────────────")
for sym, desc in [("pinmux_enable_setting", "pinmux enable setting"),
                  ("pinconf_apply_setting", "pinconf apply setting"),
                  ("pinmux_gpio_request", "pinmux gpio request")]:
    record(f"{desc} ({sym})", symbol_exists(sym))

# ── Step 9: pinconf-pins debugfs readability ─────────────────────────
print("\n── Step 9: pinconf-pins per-controller ───────────────────────")
for d in pctrl_dirs[:2]:
    p = f"{d}pinconf-pins"
    n = os.path.basename(d.rstrip('/'))
    if os.path.exists(p):
        v = sysfs_read(p)
        record(f"  {n}/pinconf-pins", v is not None,
               f"{len(v.split(chr(10))) if v else 0} lines")
    else:
        record(f"  {n}/pinconf-pins", False, "absent (controller may not support pinconf)")

# ── Step 10: SoC driver symbols ─────────────────────────────────────
print("\n── Step 10: SoC pin controller driver symbols ────────────────")
for sym, desc in [("intel_pinctrl_probe", "Intel PCH pinctrl"),
                  ("amd_pinctrl_probe", "AMD FCH pinctrl"),
                  ("rockchip_pinctrl_probe", "Rockchip IOMUX"),
                  ("msm_pinctrl_probe", "Qualcomm TLMM"),
                  ("imx_pinctrl_probe", "NXP i.MX IOMUXC")]:
    record(f"{desc} ({sym})", symbol_exists(sym))

print("\n" + "=" * 64)
print("  SUMMARY")
print("=" * 64)
p = sum(1 for _, ok, _ in results if ok)
f = len(results) - p
print(f"  PASS: {p}/{len(results)}   FAIL: {f}/{len(results)}")
if f:
    for n, ok, d in results:
        if not ok: print(f"    - {n}" + (f": {d}" if d else ""))
print("  NOTE: pinctrl_select_state fires on device probe/suspend/resume.")
print("  SoC driver symbols depend on which pinctrl driver is loaded.")
print("=" * 64)
sys.exit(0 if not f else 1)
