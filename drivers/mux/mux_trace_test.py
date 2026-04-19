#!/usr/bin/env python3
"""mux subsystem verification."""
import subprocess,os,sys,glob,tempfile,re

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

def sysfs_read(p):
    try:
        with open(p) as f: return f.read().strip()
    except: return None

print("="*60)
print("  mux Subsystem Verification  (drivers/mux/)")
print("="*60)

record("running as root",check_root())
record("bpftrace available",bpftrace_available())

# Core symbols
for sym in ["mux_control_select","mux_control_deselect",
            "mux_chip_alloc","mux_chip_register","devm_mux_control_get"]:
    record(f"symbol {sym}",symbol_exists(sym))

# sysfs bus
mux_devs=glob.glob("/sys/bus/platform/devices/*/mux*")
mux_class=glob.glob("/sys/class/mux/*")
record("mux devices or class entries",len(mux_devs)+len(mux_class)>0,
       f"devs={len(mux_devs)} class={len(mux_class)}" if mux_devs or mux_class
       else "no mux hardware (GPIO/MMIO mux not configured)")

# kprobe on mux_control_select
if bpftrace_available() and symbol_exists("mux_control_select"):
    script="""
kprobe:mux_control_select { @sel[comm]=count(); }
interval:s:5 { print(@sel); printf("SEL_DONE\\n"); exit(); }
"""
    out=run_bpftrace(script,timeout=9)
    done="SEL_DONE" in out
    record("mux_control_select kprobe compiles",done)
    fired=bool(re.search(r':\s*[1-9]',out)) and "sel" in out
    record("mux_control_select fired",fired,
           "no mux operations in window" if not fired else "")

# Driver symbols
for sym,desc in [("gpio_mux_probe","GPIO mux"),
                 ("mmio_mux_probe","MMIO mux")]:
    record(f"{desc} ({sym})",symbol_exists(sym))

print("\n"+"="*60+"  SUMMARY\n"+"="*60)
p=sum(1 for _,ok,_ in results if ok); f=len(results)-p
print(f"  PASS: {p}/{len(results)}   FAIL: {f}/{len(results)}")
if f:
    for n,ok,d in results:
        if not ok: print(f"    - {n}"+(f": {d}" if d else ""))
print("  NOTE: mux operations need hardware with DT mux-controls binding.")
print("="*60)
sys.exit(0 if not f else 1)
