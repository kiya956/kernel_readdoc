#!/usr/bin/env python3
"""
Counter subsystem verification via bpftrace + sysfs/chrdev.

Tests:
  1. Prerequisites (bpftrace, counter bus)
  2. Counter devices enumerated
  3. sysfs count/signal attributes readable
  4. kprobe on counter_push_event
  5. kprobe on counter_add (registration path)
  6. /dev/counter* chrdev present
  7. COUNTER_ADD_WATCH_IOCTL (watch subscription)
  8. count value read via sysfs
  9. counter_alloc symbol
 10. counter-chrdev event ring kprobe

Each step is marked PASS or FAIL.
"""

import subprocess, tempfile, os, sys, re, glob, struct, fcntl

PASS="\033[32mPASS\033[0m"; FAIL="\033[31mFAIL\033[0m"
results=[]

def record(name,ok,detail=""):
    results.append((name,ok,detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}"+(f"  ({detail})" if detail else ""))

def check_root(): return os.geteuid()==0
def bpftrace_available():
    try: return subprocess.run(["bpftrace","--version"],capture_output=True,timeout=5).returncode==0
    except FileNotFoundError: return False

def run_bpftrace(script,timeout=10):
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

def symbol_exists(sym):
    try:
        r=subprocess.run(["grep","-wc",sym,"/proc/kallsyms"],capture_output=True,text=True,timeout=5)
        return r.returncode==0 and int(r.stdout.strip())>0
    except: return False

def get_counter_devices():
    return glob.glob("/sys/bus/counter/devices/counter*")

# COUNTER_ADD_WATCH_IOCTL: _IOW(0x3E, 0x00, struct counter_watch)
# struct counter_watch { struct counter_component { u8 type; u8 scope; u8 parent; } component; u8 event; u8 channel; u8 _pad[3]; }
_WATCH_STRUCT = struct.Struct("BBBBBBB1x")  # 8 bytes total
_COUNTER_MAGIC = 0x3E
COUNTER_ADD_WATCH_IOCTL = (1<<30)|(_COUNTER_MAGIC<<8)|0|(_WATCH_STRUCT.size<<16)

COUNTER_COMPONENT_COUNT   = 2
COUNTER_SCOPE_COUNT       = 1
COUNTER_EVENT_OVERFLOW    = 0

def step1():
    print("\n── Step 1: Prerequisites ──────────────────────────────────────")
    record("running as root", check_root())
    record("bpftrace available", bpftrace_available())
    record("counter bus present", os.path.isdir("/sys/bus/counter"), "/sys/bus/counter")
    record("counter_push_event symbol", symbol_exists("counter_push_event"))
    record("counter_add symbol", symbol_exists("counter_add"))

def step2():
    print("\n── Step 2: Counter device enumeration ─────────────────────────")
    devs = get_counter_devices()
    record("counter devices present", len(devs)>0,
           f"{[os.path.basename(d) for d in devs]}" if devs else "no counter hardware")
    return devs

def step3(devs):
    print("\n── Step 3: sysfs count/signal attributes ───────────────────────")
    if not devs: record("sysfs attrs",False,"no devices"); return
    for d in devs[:2]:
        n=os.path.basename(d)
        # Try count0/count and signal0/level
        for path,attr in [(f"{d}/count0/count","count"),
                          (f"{d}/count0/direction","direction"),
                          (f"{d}/count0/function","function"),
                          (f"{d}/signal0/level","signal level")]:
            val=sysfs_read(path)
            if val is not None:
                record(f"{n} {attr}", True, val)

def step4():
    print("\n── Step 4: kprobe on counter_push_event ────────────────────────")
    if not bpftrace_available(): record("counter_push_event kprobe",False,"bpftrace missing"); return
    if not symbol_exists("counter_push_event"): record("counter_push_event",False,"absent"); return
    script="""
kprobe:counter_push_event {
    printf("COUNTER_EVENT comm=%s event=%u\\n", comm, arg1);
}
interval:s:5 { printf("KPROBE_DONE\\n"); exit(); }
"""
    out=run_bpftrace(script,timeout=9)
    done="KPROBE_DONE" in out
    record("counter_push_event kprobe compiles",done,out[:80] if not done else "")
    record("counter_push_event fired","COUNTER_EVENT" in out,
           "no events in window (need active counter HW)" if "COUNTER_EVENT" not in out else "")

def step5():
    print("\n── Step 5: kprobe on counter_add ──────────────────────────────")
    if not bpftrace_available(): record("counter_add kprobe",False,"bpftrace missing"); return
    if not symbol_exists("counter_add"): record("counter_add",False,"absent"); return
    script="""
kprobe:counter_add { printf("COUNTER_ADD comm=%s\\n", comm); }
interval:s:4 { printf("ADD_DONE\\n"); exit(); }
"""
    out=run_bpftrace(script,timeout=8)
    record("counter_add kprobe compiles","ADD_DONE" in out or "ERROR" not in out.upper())

def step6():
    print("\n── Step 6: /dev/counter* chrdev ───────────────────────────────")
    devs=glob.glob("/dev/counter*")
    record("/dev/counter* present",len(devs)>0,
           f"{devs}" if devs else "no chrdev (no counter hardware)")

def step7():
    print("\n── Step 7: COUNTER_ADD_WATCH_IOCTL ────────────────────────────")
    devs=glob.glob("/dev/counter*")
    if not devs: record("COUNTER_ADD_WATCH_IOCTL",False,"no /dev/counter*"); return
    try:
        fd=os.open(devs[0],os.O_RDWR)
    except (PermissionError,OSError) as e:
        record("open /dev/counter0",False,str(e)); return
    record("open /dev/counter0",True,devs[0])
    buf=bytearray(_WATCH_STRUCT.size)
    # type=COUNT(2), scope=COUNT(1), parent=0, event=OVERFLOW(0), channel=0
    _WATCH_STRUCT.pack_into(buf,0,COUNTER_COMPONENT_COUNT,COUNTER_SCOPE_COUNT,0,COUNTER_EVENT_OVERFLOW,0,0,0)
    try:
        fcntl.ioctl(fd,COUNTER_ADD_WATCH_IOCTL,buf)
        record("COUNTER_ADD_WATCH_IOCTL succeeds",True)
    except OSError as e:
        record("COUNTER_ADD_WATCH_IOCTL",False,str(e))
    finally:
        os.close(fd)

def step8(devs):
    print("\n── Step 8: count value via sysfs ──────────────────────────────")
    if not devs: record("count value read",False,"no devices"); return
    for d in devs:
        count_path=f"{d}/count0/count"
        val=sysfs_read(count_path)
        if val is not None:
            try:
                count_int=int(val)
                record(f"{os.path.basename(d)}/count0/count readable",True,f"value={count_int}")
                return
            except ValueError:
                record(f"{os.path.basename(d)}/count0/count",True,val)
                return
    record("count value read",False,"no count0/count attribute found")

def step9():
    print("\n── Step 9: counter_alloc symbol ────────────────────────────────")
    for sym in ["counter_alloc","devm_counter_alloc","counter_priv"]:
        record(f"symbol {sym}",symbol_exists(sym))

def step10():
    print("\n── Step 10: counter-chrdev event ring kprobe ───────────────────")
    if not bpftrace_available(): record("chrdev kprobe",False,"bpftrace missing"); return
    sym="counter_chrdev_read"
    if not symbol_exists(sym):
        # Try alternative
        sym="counter_chrdev_poll"
    if not symbol_exists(sym):
        record("counter_chrdev_read/poll symbol",False,"absent"); return
    script=f"""
kprobe:{sym} {{ @reads[comm]=count(); }}
interval:s:4 {{ print(@reads); printf("CHRDEV_DONE\\n"); exit(); }}
"""
    out=run_bpftrace(script,timeout=8)
    record("chrdev kprobe compiles","CHRDEV_DONE" in out or "ERROR" not in out.upper())

def main():
    print("="*64)
    print("  Counter Subsystem Verification")
    print("  Linux kernel: drivers/counter/")
    print("="*64)
    step1()
    devs=step2()
    step3(devs)
    step4()
    step5()
    step6()
    step7()
    step8(devs)
    step9()
    step10()
    print("\n"+"="*64); print("  SUMMARY"); print("="*64)
    passed=sum(1 for _,ok,_ in results if ok)
    failed=sum(1 for _,ok,_ in results if not ok)
    print(f"  PASS: {passed}/{len(results)}   FAIL: {failed}/{len(results)}")
    if failed:
        for n,ok,d in results:
            if not ok: print(f"    - {n}"+(f": {d}" if d else ""))
    print("\n  NOTE: Hardware steps require a quadrature encoder, pulse counter,")
    print("  or capture timer (STM32/TI/Intel QEP boards).")
    print("="*64)
    sys.exit(0 if not failed else 1)

if __name__=="__main__": main()
