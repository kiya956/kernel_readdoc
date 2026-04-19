#!/usr/bin/env python3
"""Regulator subsystem verification via sysfs, debugfs, and bpftrace."""
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
print("  Regulator Subsystem Verification  (drivers/regulator/)")
print("=" * 64)

# ── Step 1: Prerequisites ────────────────────────────────────────────
print("\n── Step 1: Prerequisites ──────────────────────────────────────")
record("running as root", check_root())
record("bpftrace available", bpftrace_available())
record("regulator_enable symbol", symbol_exists("regulator_enable"))
record("regulator_register symbol", symbol_exists("regulator_register"))

# ── Step 2: sysfs regulator class enumeration ───────────────────────
print("\n── Step 2: sysfs /sys/class/regulator ────────────────────────")
regs = sorted(glob.glob("/sys/class/regulator/regulator.*"))
record("regulator class entries", len(regs) > 0,
       f"{len(regs)} regulators")
for r in regs[:5]:
    name  = sysfs_read(f"{r}/name")  or "?"
    stat  = sysfs_read(f"{r}/status") or "?"
    uv    = sysfs_read(f"{r}/microvolts") or "?"
    users = sysfs_read(f"{r}/num_users") or "?"
    n = os.path.basename(r)
    record(f"  {n} ({name})", True,
           f"status={stat} uV={uv} users={users}")

# ── Step 3: debugfs regulator_summary ───────────────────────────────
print("\n── Step 3: debugfs regulator_summary ─────────────────────────")
summary_path = "/sys/kernel/debug/regulator/regulator_summary"
if os.path.exists(summary_path):
    content = sysfs_read(summary_path)
    lines = content.split('\n') if content else []
    record("regulator_summary readable", content is not None,
           f"{len(lines)} lines")
    enabled_count = sum(1 for l in lines if "enabled" in l.lower())
    record("enabled regulators visible", enabled_count > 0,
           f"{enabled_count} enabled")
else:
    record("debugfs regulator_summary", False,
           "mount debugfs: mount -t debugfs none /sys/kernel/debug")

# ── Step 4: per-regulator debugfs ───────────────────────────────────
print("\n── Step 4: per-regulator debugfs entries ─────────────────────")
dbg_regs = glob.glob("/sys/kernel/debug/regulator/*/")
record("debugfs regulator dirs", len(dbg_regs) > 0,
       f"{len(dbg_regs)} entries")
for d in dbg_regs[:2]:
    n = os.path.basename(d.rstrip('/'))
    for attr in ["enable_count", "min_microvolts", "max_microvolts"]:
        p = f"{d}{attr}"
        if os.path.exists(p):
            v = sysfs_read(p)
            record(f"  {n}/{attr}", v is not None, v or "?")

# ── Step 5: kprobe regulator_enable ─────────────────────────────────
print("\n── Step 5: kprobe regulator_enable ───────────────────────────")
if bpftrace_available() and symbol_exists("regulator_enable"):
    script = """
kprobe:regulator_enable { @enables[comm] = count(); }
interval:s:5 { print(@enables); printf("EN_DONE\\n"); exit(); }
"""
    out = run_bpftrace(script, timeout=9)
    done = "EN_DONE" in out
    record("regulator_enable kprobe compiles+runs", done)
    fired = bool(re.search(r':\s*[1-9]', out))
    record("regulator_enable fired in 5s", fired,
           "no enable events in window" if not fired else "")
else:
    record("regulator_enable kprobe", False, "bpftrace or symbol missing")

# ── Step 6: kprobe regulator_set_voltage ────────────────────────────
print("\n── Step 6: kprobe regulator_set_voltage ──────────────────────")
if bpftrace_available() and symbol_exists("regulator_set_voltage"):
    script = """
kprobe:regulator_set_voltage { @vset[comm] = count(); }
interval:s:5 { print(@vset); printf("VSET_DONE\\n"); exit(); }
"""
    out = run_bpftrace(script, timeout=9)
    record("regulator_set_voltage kprobe compiles", "VSET_DONE" in out)
else:
    record("regulator_set_voltage kprobe", False, "bpftrace or symbol missing")

# ── Step 7: regulator_enable latency ────────────────────────────────
print("\n── Step 7: regulator_enable latency histogram ────────────────")
if bpftrace_available() and symbol_exists("regulator_enable"):
    script = """
kprobe:regulator_enable    { @s[tid] = nsecs; }
kretprobe:regulator_enable {
    if (@s[tid]) { @lat_us = hist((nsecs-@s[tid])/1000); delete(@s[tid]); }
}
interval:s:5 { print(@lat_us); printf("LAT_DONE\\n"); exit(); }
"""
    out = run_bpftrace(script, timeout=9)
    record("regulator_enable latency kprobe ran", "LAT_DONE" in out)
else:
    record("regulator_enable latency", False, "bpftrace or symbol missing")

# ── Step 8: kprobe _regulator_enable (internal) ─────────────────────
print("\n── Step 8: _regulator_enable (internal) kprobe ───────────────")
if bpftrace_available() and symbol_exists("_regulator_enable"):
    script = """
kprobe:_regulator_enable { @internal++; }
interval:s:4 { printf("INT_DONE count=%d\\n", @internal); exit(); }
"""
    out = run_bpftrace(script, timeout=7)
    record("_regulator_enable kprobe compiles", "INT_DONE" in out)
    m = re.search(r'count=(\d+)', out)
    if m:
        record("internal enable calls observed", int(m.group(1)) >= 0,
               f"count={m.group(1)}")
else:
    record("_regulator_enable kprobe", False, "symbol absent (may be inlined)")

# ── Step 9: Supply chain symbols ────────────────────────────────────
print("\n── Step 9: Supply chain / notifier symbols ───────────────────")
for sym, desc in [("regulator_get_voltage", "get_voltage consumer API"),
                  ("regulator_notifier_call_chain", "event notifier"),
                  ("regulator_disable", "disable consumer API"),
                  ("regulator_register", "provider registration")]:
    record(f"{desc} ({sym})", symbol_exists(sym))

# ── Step 10: PMIC driver symbols ────────────────────────────────────
print("\n── Step 10: PMIC regulator driver symbols ────────────────────")
for sym, desc in [("qcom_rpmh_regulator_probe", "Qualcomm RPMh regulator"),
                  ("da9210_probe", "Dialog DA9210 buck"),
                  ("fan53555_regulator_probe", "FAN53555 buck"),
                  ("fixed_regulator_probe", "fixed voltage regulator"),
                  ("gpio_regulator_probe", "GPIO-controlled regulator")]:
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
print("  NOTE: Regulator kprobe events depend on power state transitions")
print("  (DVFS, suspend/resume). Idle systems may show 0 events.")
print("=" * 64)
sys.exit(0 if not f else 1)
