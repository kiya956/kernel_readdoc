#!/usr/bin/env python3
"""
RPMsg (Remote Processor Messaging) subsystem verification.

Tests:
  1. Prerequisites (bpftrace, rpmsg bus)
  2. rpmsg devices enumerated
  3. rpmsg_ctrl dev node present
  4. rpmsg_char dev nodes
  5. kprobe on rpmsg_send
  6. kprobe on rpmsg_create_ept
  7. rpmsg_send latency histogram
  8. Name service symbol (rpmsg_ns_announce)
  9. VirtIO rpmsg backend symbols
 10. Qualcomm GLINK/SMD symbols

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

def kprobe_compiles(sym, timeout=6):
    script=f"""kprobe:{sym} {{ @c++; }}
interval:s:{timeout-1} {{ printf("DONE\\n"); exit(); }}"""
    out=run_bpftrace(script,timeout=timeout+2)
    return "DONE" in out and "ERROR" not in out.upper()

# RPMSG_CREATE_EPT_IOCTL: _IOW(0xb5, 0x1, struct rpmsg_endpoint_info)
# struct rpmsg_endpoint_info { char name[32]; __u32 src; __u32 dst; }
_EPT_STRUCT = struct.Struct("32sII")
RPMSG_CREATE_EPT_IOCTL = (1<<30)|(0xb5<<8)|1|(_EPT_STRUCT.size<<16)

def step1():
    print("\n── Step 1: Prerequisites ──────────────────────────────────────")
    record("running as root", check_root())
    record("bpftrace available", bpftrace_available())
    record("rpmsg bus present", os.path.isdir("/sys/bus/rpmsg"), "/sys/bus/rpmsg")
    record("rpmsg_send symbol", symbol_exists("rpmsg_send"))

def step2():
    print("\n── Step 2: rpmsg device enumeration ───────────────────────────")
    devs=glob.glob("/sys/bus/rpmsg/devices/*")
    record("rpmsg devices present", len(devs)>0,
           f"{[os.path.basename(d) for d in devs[:3]]}" if devs
           else "no rpmsg devices (no remote processor running)")
    for d in devs[:2]:
        n=os.path.basename(d)
        for attr in ["name","src","dst"]:
            val=sysfs_read(f"{d}/{attr}")
            if val: record(f"  {n}/{attr}",True,val)
    return devs

def step3():
    print("\n── Step 3: rpmsg_ctrl device node ─────────────────────────────")
    ctrl_devs=glob.glob("/dev/rpmsg_ctrl*")
    record("rpmsg_ctrl dev node present",len(ctrl_devs)>0,
           f"{ctrl_devs}" if ctrl_devs else "no rpmsg_ctrl (no remote proc)")

def step4():
    print("\n── Step 4: /dev/rpmsg* char devices ───────────────────────────")
    rpmsg_devs=[d for d in glob.glob("/dev/rpmsg*") if "ctrl" not in d]
    record("/dev/rpmsg* char devices",len(rpmsg_devs)>0,
           f"{rpmsg_devs}" if rpmsg_devs else "no char devices (endpoints not open)")

def step5():
    print("\n── Step 5: kprobe on rpmsg_send ───────────────────────────────")
    if not bpftrace_available(): record("rpmsg_send kprobe",False,"bpftrace missing"); return
    if not symbol_exists("rpmsg_send"): record("rpmsg_send",False,"absent"); return
    script="""
kprobe:rpmsg_send {
    printf("RPMSG_SEND pid=%d comm=%s len=%d\\n", pid, comm, arg2);
}
interval:s:5 { printf("SEND_DONE\\n"); exit(); }
"""
    out=run_bpftrace(script,timeout=9)
    done="SEND_DONE" in out
    record("rpmsg_send kprobe compiles",done,out[:80] if not done else "")
    record("rpmsg_send fired","RPMSG_SEND" in out,
           "no messages in window (need active remote proc)" if "RPMSG_SEND" not in out else "")

def step6():
    print("\n── Step 6: kprobe on rpmsg_create_ept ─────────────────────────")
    if not bpftrace_available(): record("rpmsg_create_ept kprobe",False,"bpftrace missing"); return
    if not symbol_exists("rpmsg_create_ept"): record("rpmsg_create_ept",False,"absent"); return
    ok=kprobe_compiles("rpmsg_create_ept")
    record("rpmsg_create_ept kprobe compiles+runs",ok)

def step7():
    print("\n── Step 7: rpmsg_send latency histogram ────────────────────────")
    if not bpftrace_available(): record("rpmsg latency",False,"bpftrace missing"); return
    if not symbol_exists("rpmsg_send"): record("rpmsg_send",False,"absent"); return
    script="""
kprobe:rpmsg_send    { @s[tid]=nsecs; }
kretprobe:rpmsg_send {
    if(@s[tid]){ @lat=hist((nsecs-@s[tid])/1000); delete(@s[tid]); }
}
interval:s:7 { print(@lat); printf("LAT_DONE\\n"); exit(); }
"""
    out=run_bpftrace(script,timeout=11)
    record("rpmsg_send latency kprobe ran","LAT_DONE" in out)
    record("rpmsg_send latency histogram","@lat" in out or "[" in out,
           "no sends in window" if "@lat" not in out else "")

def step8():
    print("\n── Step 8: Name service symbols ───────────────────────────────")
    for sym in ["rpmsg_ns_register_device","rpmsg_find_device"]:
        record(f"symbol {sym}",symbol_exists(sym))

def step9():
    print("\n── Step 9: VirtIO rpmsg backend symbols ────────────────────────")
    for sym in ["virtio_rpmsg_probe","rpmsg_sg_init","rpmsg_recv_done"]:
        record(f"symbol {sym}",symbol_exists(sym),
               "virtio_rpmsg_bus not loaded" if not symbol_exists(sym) else "")

def step10():
    print("\n── Step 10: Qualcomm GLINK/SMD symbols ────────────────────────")
    for sym in ["qcom_glink_smem_register","qcom_smd_register_edge"]:
        record(f"symbol {sym}",symbol_exists(sym),
               "not present on non-Qualcomm platform" if not symbol_exists(sym) else "")
    # MediaTek
    record("mtk_rpmsg symbol",symbol_exists("mtk_rpmsg_probe"))

def main():
    print("="*64)
    print("  RPMsg Subsystem Verification")
    print("  Linux kernel: drivers/rpmsg/")
    print("="*64)
    step1(); step2(); step3(); step4(); step5()
    step6(); step7(); step8(); step9(); step10()
    print("\n"+"="*64); print("  SUMMARY"); print("="*64)
    passed=sum(1 for _,ok,_ in results if ok)
    failed=sum(1 for _,ok,_ in results if not ok)
    print(f"  PASS: {passed}/{len(results)}   FAIL: {failed}/{len(results)}")
    if failed:
        for n,ok,d in results:
            if not ok: print(f"    - {n}"+(f": {d}" if d else ""))
    print("\n  NOTE: Device/channel steps need a running remote processor")
    print("  (DSP, MCU, modem) loaded via remoteproc or Qualcomm GLINK.")
    print("="*64)
    sys.exit(0 if not failed else 1)

if __name__=="__main__": main()
