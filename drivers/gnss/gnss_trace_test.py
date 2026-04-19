#!/usr/bin/env python3
"""GNSS subsystem verification."""
import subprocess,os,sys,glob,tempfile

PASS="\033[32mPASS\033[0m"; FAIL="\033[31mFAIL\033[0m"
results=[]

def record(name,ok,detail=""):
    results.append((name,ok,detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}"+(f"  ({detail})" if detail else ""))

def check_root(): return os.geteuid()==0
def symbol_exists(sym):
    try:
        r=subprocess.run(["grep","-wc",sym,"/proc/kallsyms"],capture_output=True,text=True,timeout=5)
        return r.returncode==0 and int(r.stdout.strip())>0
    except: return False

def bpftrace_available():
    try: return subprocess.run(["bpftrace","--version"],capture_output=True,timeout=5).returncode==0
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
print("  GNSS Subsystem Verification  (drivers/gnss/)")
print("="*60)

record("running as root",check_root())
record("bpftrace available",bpftrace_available())

# Device nodes
gnss_devs=glob.glob("/dev/gnss*")
record("/dev/gnss* present",len(gnss_devs)>0,
       f"{gnss_devs}" if gnss_devs else "no GNSS receiver")

# Try reading NMEA data (non-blocking)
for dev in gnss_devs[:1]:
    try:
        fd=os.open(dev,os.O_RDONLY|os.O_NONBLOCK)
        try:
            data=os.read(fd,256)
            record(f"{dev} NMEA read",len(data)>0,data[:40].decode(errors="replace"))
        except BlockingIOError:
            record(f"{dev} readable (no data yet)",True,"O_NONBLOCK: no data in buffer")
        finally: os.close(fd)
    except PermissionError as e: record(f"{dev} open",False,str(e))

# sysfs
gnss_sysfs=glob.glob("/sys/class/gnss/gnss*")
record("gnss sysfs class",len(gnss_sysfs)>0,
       f"{[os.path.basename(d) for d in gnss_sysfs]}" if gnss_sysfs else "none")
for d in gnss_sysfs[:1]:
    for attr in ["type","power_on"]:
        p=f"{d}/{attr}"
        if os.path.exists(p):
            try:
                with open(p) as f: v=f.read().strip()
                record(f"{os.path.basename(d)}/{attr}",True,v)
            except: pass

# Symbols
for sym in ["gnss_allocate_device","gnss_register_device","gnss_receive_buf"]:
    record(f"symbol {sym}",symbol_exists(sym))

# kprobe
if bpftrace_available() and symbol_exists("gnss_receive_buf"):
    script="""
kprobe:gnss_receive_buf { printf("GNSS_RX count=%d\\n",@c++); }
interval:s:5 { printf("KPROBE_DONE\\n"); exit(); }
"""
    out=run_bpftrace(script,timeout=9)
    record("gnss_receive_buf kprobe compiles","KPROBE_DONE" in out)
    record("gnss_receive_buf fired in 5s","GNSS_RX" in out,
           "no GNSS data in window" if "GNSS_RX" not in out else "")

print("\n"+"="*60+"  SUMMARY\n"+"="*60)
p=sum(1 for _,ok,_ in results if ok); f=len(results)-p
print(f"  PASS: {p}/{len(results)}   FAIL: {f}/{len(results)}")
if f:
    for n,ok,d in results:
        if not ok: print(f"    - {n}"+(f": {d}" if d else ""))
print("  NOTE: Requires GNSS receiver hardware (USB GPS dongle, UART module).")
print("="*60)
sys.exit(0 if not f else 1)
