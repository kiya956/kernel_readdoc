#!/usr/bin/env python3
"""hwspinlock subsystem verification."""
import subprocess,os,sys,glob,tempfile

PASS="\033[32mPASS\033[0m"; FAIL="\033[31mFAIL\033[0m"
results=[]

def record(name,ok,detail=""):
    results.append((name,ok,detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}"+(f"  ({detail})" if detail else ""))

def check_root(): return os.geteuid()==0
def bpftrace_available():
    try: return subprocess.run(["bpftrace","--version"],capture_output=True,timeout=5).returncode==0
    except: return False

def symbol_exists(sym):
    try:
        r=subprocess.run(["grep","-wc",sym,"/proc/kallsyms"],capture_output=True,text=True,timeout=5)
        return r.returncode==0 and int(r.stdout.strip())>0
    except: return False

def run_bpftrace(script,timeout=8):
    with tempfile.NamedTemporaryFile("w",suffix=".bt",delete=False) as f:
        f.write(script); fname=f.name
    try:
        r=subprocess.run(["bpftrace",fname],capture_output=True,text=True,timeout=timeout)
        return r.stdout+r.stderr
    except subprocess.TimeoutExpired: return ""
    finally: os.unlink(fname)

print("="*60)
print("  hwspinlock Subsystem Verification  (drivers/hwspinlock/)")
print("="*60)

record("running as root",check_root())
record("bpftrace available",bpftrace_available())

# Core symbols
for sym in ["hwspin_lock_timeout","hwspin_trylock","hwspin_unlock",
            "__hwspin_lock","hwspin_lock_register"]:
    record(f"symbol {sym}",symbol_exists(sym))

# sysfs bus
record("hwspinlock bus",os.path.isdir("/sys/bus/platform"),"/sys/bus/platform")

# Platform driver symbols
for sym,desc in [("omap_hwspinlock_probe","OMAP hwspinlock"),
                 ("qcom_hwspinlock_probe","Qualcomm hwspinlock"),
                 ("stm32_hwspinlock_probe","STM32 HSEM"),
                 ("sun6i_hwspinlock_probe","Allwinner hwspinlock")]:
    if symbol_exists(sym):
        record(f"{desc} ({sym})",True)

# kprobe
if bpftrace_available() and symbol_exists("__hwspin_lock"):
    script="""
kprobe:__hwspin_lock { @lock_count++; }
interval:s:5 { printf("HWSPIN count=%d\\nDONE\\n",@lock_count); exit(); }
"""
    out=run_bpftrace(script,timeout=9)
    done="DONE" in out
    record("__hwspin_lock kprobe compiles+runs",done)
    import re
    m=re.search(r'count=(\d+)',out)
    if m: record("hwspinlock operations observed",int(m.group(1))>0,f"count={m.group(1)}")

print("\n"+"="*60+"  SUMMARY\n"+"="*60)
p=sum(1 for _,ok,_ in results if ok); f=len(results)-p
print(f"  PASS: {p}/{len(results)}   FAIL: {f}/{len(results)}")
if f:
    for n,ok,d in results:
        if not ok: print(f"    - {n}"+(f": {d}" if d else ""))
print("  NOTE: Actual locks need SoC hardware (OMAP/Qualcomm/STM32/Allwinner).")
print("="*60)
sys.exit(0 if not f else 1)
