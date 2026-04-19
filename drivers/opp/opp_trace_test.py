#!/usr/bin/env python3
"""
OPP (Operating Performance Points) subsystem verification.

Tests:
  1. Prerequisites (OPP symbols, CONFIG_PM_OPP)
  2. kprobe on dev_pm_opp_set_rate
  3. kprobe on dev_pm_opp_find_freq_ceil
  4. OPP entries via cpufreq (most visible OPP consumer)
  5. dev_pm_opp_set_rate latency histogram
  6. cpufreq available_frequencies = OPP table
  7. scaling_governor and scaling_cur_freq readable
  8. OPP notifier symbol
  9. OPP of.c symbol (DT parsing)
 10. Voltage scaling: regulator_set_voltage symbol

Each step is marked PASS or FAIL.
"""

import subprocess, tempfile, os, sys, re, glob

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
results = []

def record(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))

def check_root(): return os.geteuid() == 0
def bpftrace_available():
    try: return subprocess.run(["bpftrace","--version"],capture_output=True,timeout=5).returncode==0
    except FileNotFoundError: return False

def run_bpftrace(script, timeout=10):
    with tempfile.NamedTemporaryFile("w", suffix=".bt", delete=False) as f:
        f.write(script); fname=f.name
    try:
        r=subprocess.run(["bpftrace",fname],capture_output=True,text=True,timeout=timeout)
        return r.stdout+r.stderr
    except subprocess.TimeoutExpired: return ""
    finally: os.unlink(fname)

def sysfs_read(p):
    try:
        with open(p) as f: return f.read().strip()
    except: return None

def symbol_exists(sym):
    try:
        r=subprocess.run(["grep","-wc",sym,"/proc/kallsyms"],capture_output=True,text=True,timeout=5)
        return r.returncode==0 and int(r.stdout.strip())>0
    except: return False

def kprobe_runs(sym, timeout=7):
    script=f"""
kprobe:{sym} {{ @c++; }}
interval:s:{timeout-1} {{ printf("DONE c=%d\\n",@c); exit(); }}
"""
    out=run_bpftrace(script,timeout=timeout+2)
    return "DONE" in out, re.search(r'c=(\d+)',out)

# ── Steps ──

def step1():
    print("\n── Step 1: Prerequisites ──────────────────────────────────────")
    record("running as root", check_root())
    record("bpftrace available", bpftrace_available())
    for sym in ["dev_pm_opp_set_rate","dev_pm_opp_find_freq_ceil","dev_pm_opp_get_opp_table"]:
        record(f"symbol {sym}", symbol_exists(sym))

def step2():
    print("\n── Step 2: kprobe dev_pm_opp_set_rate ─────────────────────────")
    if not bpftrace_available(): record("opp_set_rate kprobe","bpftrace missing"); return
    if not symbol_exists("dev_pm_opp_set_rate"): record("dev_pm_opp_set_rate",False,"absent"); return
    ok, m = kprobe_runs("dev_pm_opp_set_rate")
    record("dev_pm_opp_set_rate kprobe compiles+runs", ok)
    if m: record("dev_pm_opp_set_rate called", int(m.group(1))>0, f"count={m.group(1)}")

def step3():
    print("\n── Step 3: kprobe dev_pm_opp_find_freq_ceil ───────────────────")
    if not bpftrace_available(): record("find_freq_ceil kprobe",False,"bpftrace missing"); return
    if not symbol_exists("dev_pm_opp_find_freq_ceil"): record("find_freq_ceil",False,"absent"); return
    ok, m = kprobe_runs("dev_pm_opp_find_freq_ceil")
    record("dev_pm_opp_find_freq_ceil kprobe runs", ok)
    if m: record("find_freq_ceil called", int(m.group(1))>0, f"count={m.group(1)}")

def step4():
    print("\n── Step 4: OPP via cpufreq available_frequencies ──────────────")
    cpufreq_dirs = glob.glob("/sys/devices/system/cpu/cpufreq/policy*")
    if not cpufreq_dirs: record("cpufreq policies", False, "no policies"); return
    for d in cpufreq_dirs[:2]:
        name = os.path.basename(d)
        avail = sysfs_read(f"{d}/scaling_available_frequencies")
        if avail:
            freqs = avail.split()
            record(f"{name} OPP count", len(freqs) > 1, f"{len(freqs)} OPPs: {freqs[:4]}")

def step5():
    print("\n── Step 5: dev_pm_opp_set_rate latency histogram ──────────────")
    if not bpftrace_available(): record("opp latency",False,"bpftrace missing"); return
    if not symbol_exists("dev_pm_opp_set_rate"): record("opp_set_rate",False,"absent"); return
    script="""
kprobe:dev_pm_opp_set_rate    { @s[tid]=nsecs; }
kretprobe:dev_pm_opp_set_rate {
    if(@s[tid]){ @lat=hist((nsecs-@s[tid])/1000); delete(@s[tid]); }
}
interval:s:7 { print(@lat); printf("LAT_DONE\\n"); exit(); }
"""
    out=run_bpftrace(script,timeout=11)
    record("opp_set_rate latency kprobe ran","LAT_DONE" in out)
    record("opp latency histogram", "@lat" in out or "[" in out,
           "no OPP transitions in window" if "@lat" not in out else "")

def step6():
    print("\n── Step 6: cpufreq scaling_cur_freq / governor ─────────────────")
    for policy in glob.glob("/sys/devices/system/cpu/cpufreq/policy*")[:2]:
        n = os.path.basename(policy)
        for attr in ["scaling_cur_freq","scaling_governor","scaling_max_freq","scaling_min_freq"]:
            v = sysfs_read(f"{policy}/{attr}")
            if v: record(f"{n}/{attr}", True, v)

def step7():
    print("\n── Step 7: OPP notifier symbol ────────────────────────────────")
    for sym in ["dev_pm_opp_register_notifier","blocking_notifier_call_chain"]:
        record(f"symbol {sym}", symbol_exists(sym))

def step8():
    print("\n── Step 8: OPP DT parsing (of.c) ──────────────────────────────")
    for sym in ["dev_pm_opp_of_add_table","of_get_required_opp_performance_state"]:
        record(f"symbol {sym}", symbol_exists(sym))

def step9():
    print("\n── Step 9: regulator voltage scaling integration ───────────────")
    for sym in ["regulator_set_voltage","dev_pm_opp_get_voltage"]:
        record(f"symbol {sym}", symbol_exists(sym))

def step10():
    print("\n── Step 10: OPP power / efficiency APIs ────────────────────────")
    for sym in ["dev_pm_opp_get_power","dev_pm_opp_calc_power"]:
        present = symbol_exists(sym)
        record(f"symbol {sym}", present, "CXL 3.0+ / newer kernel" if not present else "")

def main():
    print("="*64)
    print("  OPP (Operating Performance Points) Verification")
    print("  Linux kernel: drivers/opp/")
    print("="*64)
    step1(); step2(); step3(); step4(); step5()
    step6(); step7(); step8(); step9(); step10()
    print("\n"+"="*64+"  SUMMARY\n"+"="*64)
    passed=sum(1 for _,ok,_ in results if ok)
    failed=sum(1 for _,ok,_ in results if not ok)
    print(f"  PASS: {passed}/{len(results)}   FAIL: {failed}/{len(results)}")
    if failed:
        for n,ok,d in results:
            if not ok: print(f"    - {n}" + (f": {d}" if d else ""))
    print("="*64)
    sys.exit(0 if not failed else 1)

if __name__=="__main__": main()
