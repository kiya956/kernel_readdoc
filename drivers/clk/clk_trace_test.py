#!/usr/bin/env python3
"""Common Clock Framework (CCF) verification via debugfs and bpftrace."""
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
print("  CCF Clock Subsystem Verification  (drivers/clk/)")
print("=" * 64)

# ── Step 1: Prerequisites ────────────────────────────────────────────
print("\n── Step 1: Prerequisites ──────────────────────────────────────")
record("running as root", check_root())
record("bpftrace available", bpftrace_available())
record("clk_prepare_enable symbol", symbol_exists("clk_prepare_enable"))
record("clk_set_rate symbol", symbol_exists("clk_set_rate"))

# ── Step 2: debugfs clk_summary ──────────────────────────────────────
print("\n── Step 2: debugfs clk_summary ────────────────────────────────")
summary = "/sys/kernel/debug/clk/clk_summary"
if os.path.exists(summary):
    content = sysfs_read(summary) or ""
    lines = content.split('\n')
    record("clk_summary readable", bool(content), f"{len(lines)} lines")
    enabled = sum(1 for l in lines if re.search(r'\b[1-9]\d*\b.*Hz', l))
    record("enabled clocks in summary", enabled > 0, f"{enabled} clocks with rate")
    # show first few clock lines
    for l in [x for x in lines[1:6] if x.strip()]:
        print(f"    {l}")
else:
    record("debugfs clk_summary", False,
           "debugfs not mounted or CONFIG_COMMON_CLK not set")

# ── Step 3: individual clock debugfs attrs ───────────────────────────
print("\n── Step 3: per-clock debugfs attributes ──────────────────────")
clk_dirs = glob.glob("/sys/kernel/debug/clk/*/")
record("per-clock debugfs dirs", len(clk_dirs) > 0,
       f"{len(clk_dirs)} clocks")
for d in clk_dirs[:3]:
    n = os.path.basename(d.rstrip('/'))
    rate = sysfs_read(f"{d}clk_rate") or "?"
    ena  = sysfs_read(f"{d}clk_enable_count") or "?"
    record(f"  {n}", True, f"rate={rate}Hz en={ena}")

# ── Step 4: kprobe clk_prepare_enable ───────────────────────────────
print("\n── Step 4: kprobe clk_prepare_enable ─────────────────────────")
if bpftrace_available() and symbol_exists("clk_prepare_enable"):
    script = """
kprobe:clk_prepare_enable { @enables[comm] = count(); }
interval:s:5 { print(@enables); printf("EN_DONE\\n"); exit(); }
"""
    out = run_bpftrace(script, timeout=9)
    done = "EN_DONE" in out
    record("clk_prepare_enable kprobe compiles+runs", done)
    fired = bool(re.search(r':\s*[1-9]', out))
    record("clk_prepare_enable fired in 5s", fired,
           "no clock enables in window" if not fired else "")
else:
    record("clk_prepare_enable kprobe", False, "bpftrace or symbol missing")

# ── Step 5: kprobe clk_set_rate ─────────────────────────────────────
print("\n── Step 5: kprobe clk_set_rate ────────────────────────────────")
if bpftrace_available() and symbol_exists("clk_set_rate"):
    script = """
kprobe:clk_set_rate { @sets[comm] = count(); }
interval:s:5 { print(@sets); printf("SET_DONE\\n"); exit(); }
"""
    out = run_bpftrace(script, timeout=9)
    record("clk_set_rate kprobe compiles+runs", "SET_DONE" in out)
    fired = bool(re.search(r':\s*[1-9]', out))
    record("clk_set_rate fired in 5s", fired,
           "no rate changes in window" if not fired else "")
else:
    record("clk_set_rate kprobe", False, "bpftrace or symbol missing")

# ── Step 6: clk_set_rate latency histogram ───────────────────────────
print("\n── Step 6: clk_set_rate latency histogram ─────────────────────")
if bpftrace_available() and symbol_exists("clk_set_rate"):
    script = """
kprobe:clk_set_rate    { @s[tid] = nsecs; }
kretprobe:clk_set_rate {
    if (@s[tid]) { @lat_us = hist((nsecs-@s[tid])/1000); delete(@s[tid]); }
}
interval:s:5 { print(@lat_us); printf("LAT_DONE\\n"); exit(); }
"""
    out = run_bpftrace(script, timeout=9)
    record("clk_set_rate latency kprobe ran", "LAT_DONE" in out)
else:
    record("clk_set_rate latency", False, "bpftrace or symbol missing")

# ── Step 7: clk_change_rate tracepoint ──────────────────────────────
print("\n── Step 7: clk_set_rate tracepoint ───────────────────────────")
try:
    r = subprocess.run(["grep", "-r", "clk_set_rate",
                        "/sys/kernel/tracing/available_events"],
                       capture_output=True, text=True, timeout=5)
    found = "clk" in r.stdout
    record("clk tracepoints available",
           os.path.exists("/sys/kernel/tracing/events/clk"),
           "check /sys/kernel/tracing/events/clk/")
except Exception as e:
    record("clk tracepoint check", False, str(e))

clk_events = glob.glob("/sys/kernel/tracing/events/clk/*")
record("clk tracepoint events", len(clk_events) > 0,
       f"{[os.path.basename(e) for e in clk_events]}" if clk_events else "none")

# ── Step 8: clk hw type symbols ──────────────────────────────────────
print("\n── Step 8: CLK hw type symbols ────────────────────────────────")
for sym, desc in [("clk_hw_register_fixed_rate", "fixed-rate clock"),
                  ("clk_hw_register_divider", "divider clock"),
                  ("clk_hw_register_mux", "mux clock"),
                  ("clk_hw_register_gate", "gate clock"),
                  ("clk_hw_register_fixed_factor", "fixed-factor clock")]:
    record(f"{desc} ({sym})", symbol_exists(sym))

# ── Step 9: clk_get / devm_clk_get symbols ───────────────────────────
print("\n── Step 9: consumer API symbols ──────────────────────────────")
for sym, desc in [("clk_get", "clk_get lookup"),
                  ("devm_clk_get", "devm_clk_get lookup"),
                  ("clk_get_parent", "get parent clock"),
                  ("clk_round_rate", "round rate to supported value")]:
    record(f"{desc} ({sym})", symbol_exists(sym))

# ── Step 10: SoC clock driver symbols ───────────────────────────────
print("\n── Step 10: SoC clock driver symbols ─────────────────────────")
for sym, desc in [("qcom_cc_probe", "Qualcomm CC framework"),
                  ("rockchip_clk_register_pll", "Rockchip PLL"),
                  ("imx_clk_composite_flags", "NXP i.MX composite clk"),
                  ("bcm2835_clk_probe", "Broadcom BCM2835"),
                  ("mtk_clk_register_plls", "MediaTek PLL")]:
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
print("  NOTE: clk kprobe hits depend on DVFS activity during the window.")
print("  SoC driver symbols depend on which clk drivers are compiled in.")
print("=" * 64)
sys.exit(0 if not f else 1)
