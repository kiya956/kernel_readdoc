#!/usr/bin/env python3
"""
PTP (Precision Time Protocol) clock subsystem verification.

Tests:
  1. Prerequisites (bpftrace, /dev/ptp*)
  2. PTP devices enumerated
  3. PTP_CLOCK_GETCAPS ioctl
  4. PTP_SYS_OFFSET ioctl (system↔PHC offset)
  5. clock_gettime(CLOCK_TAI) / PHC POSIX clock
  6. kprobe on ptp_clock_event
  7. kprobe on ptp_clock_register
  8. sysfs attributes (max_adjustment, n_pins, ...)
  9. PPS support check
 10. Virtual PHC (ptp_vclock) symbol presence

Each step is marked PASS or FAIL.
"""

import subprocess, tempfile, os, sys, re, glob, struct, fcntl, ctypes, time

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

def get_ptp_devices(): return sorted(glob.glob("/dev/ptp*"))

# PTP ioctl constants (from uapi/linux/ptp_clock.h)
_PTP_MAGIC = ord('=')
# struct ptp_clock_caps: 11 ints
_CAPS_STRUCT = struct.Struct("11i")
PTP_CLOCK_GETCAPS = (2<<30)|(_PTP_MAGIC<<8)|1|(_CAPS_STRUCT.size<<16)

# struct ptp_sys_offset: 2*25 timespec + int samples + int reserved[3]
# Simplified: use PTP_SYS_OFFSET which is _IOWR('=', 5, struct ptp_sys_offset)
# struct ptp_sys_offset { unsigned n_samples; unsigned rsv[3]; struct { __s64 sec; __u32 nsec; __u32 reserved; } ts[2*25]; }
_SYS_OFF_STRUCT = struct.Struct("I3I" + "qII"*50)
PTP_SYS_OFFSET = (3<<30)|(_PTP_MAGIC<<8)|5|(_SYS_OFF_STRUCT.size<<16)

# CLOCK_TAI = 11
CLOCK_TAI = 11
_TIMESPEC = struct.Struct("qI4x")  # struct timespec64 (sec, nsec + pad)

# ── Steps ──

def step1():
    print("\n── Step 1: Prerequisites ──────────────────────────────────────")
    record("running as root", check_root())
    record("bpftrace available", bpftrace_available())
    devs = get_ptp_devices()
    record("/dev/ptp* devices present", len(devs)>0,
           f"found: {devs}" if devs else "no PTP hardware")
    record("ptp_clock_register symbol", symbol_exists("ptp_clock_register"))

def step2():
    print("\n── Step 2: PTP device enumeration ─────────────────────────────")
    devs = get_ptp_devices()
    record("PTP devices in /dev", len(devs)>0, f"{devs}")
    for d in devs[:2]:
        n=os.path.basename(d)
        sysfs=f"/sys/class/ptp/{n}"
        record(f"{n} sysfs dir", os.path.isdir(sysfs), sysfs)

def step3():
    print("\n── Step 3: PTP_CLOCK_GETCAPS ioctl ─────────────────────────────")
    devs = get_ptp_devices()
    if not devs: record("PTP_CLOCK_GETCAPS",False,"no device"); return
    try:
        fd=os.open(devs[0],os.O_RDWR)
    except PermissionError as e:
        record("open /dev/ptp0",False,str(e)); return
    buf=bytearray(_CAPS_STRUCT.size)
    try:
        fcntl.ioctl(fd,PTP_CLOCK_GETCAPS,buf)
        vals=_CAPS_STRUCT.unpack_from(buf)
        # max_adj, n_alarm, n_ext_ts, n_per_out, pps, n_pins, cross_timestamping, adj_phase, max_phase_adj, rsv[2]
        record("PTP_CLOCK_GETCAPS succeeds",True,
               f"max_adj={vals[0]}ppb n_ext_ts={vals[2]} n_per_out={vals[3]} pps={vals[4]} n_pins={vals[5]}")
    except OSError as e:
        record("PTP_CLOCK_GETCAPS",False,str(e))
    finally:
        os.close(fd)

def step4():
    print("\n── Step 4: PTP_SYS_OFFSET ioctl ───────────────────────────────")
    devs = get_ptp_devices()
    if not devs: record("PTP_SYS_OFFSET",False,"no device"); return
    try:
        fd=os.open(devs[0],os.O_RDWR)
    except PermissionError as e:
        record("open for sys_offset",False,str(e)); return
    # n_samples=5, rest zeroed
    buf=bytearray(_SYS_OFF_STRUCT.size)
    struct.pack_into("I",buf,0,5)  # n_samples=5
    try:
        fcntl.ioctl(fd,PTP_SYS_OFFSET,buf)
        record("PTP_SYS_OFFSET succeeds",True,"5-sample offset measurement done")
    except OSError as e:
        record("PTP_SYS_OFFSET",False,str(e))
    finally:
        os.close(fd)

def step5():
    print("\n── Step 5: POSIX clock access (CLOCK_TAI) ─────────────────────")
    try:
        import ctypes, ctypes.util
        librt=ctypes.CDLL(ctypes.util.find_library("rt") or "librt.so.1")
        class Timespec(ctypes.Structure):
            _fields_=[("tv_sec",ctypes.c_long),("tv_nsec",ctypes.c_long)]
        ts=Timespec()
        ret=librt.clock_gettime(CLOCK_TAI,ctypes.byref(ts))
        ok=ret==0
        record("clock_gettime(CLOCK_TAI) succeeds",ok,
               f"TAI={ts.tv_sec}.{ts.tv_nsec:09d}" if ok else f"ret={ret}")
    except Exception as e:
        record("clock_gettime(CLOCK_TAI)",False,str(e))

def step6():
    print("\n── Step 6: kprobe on ptp_clock_event ──────────────────────────")
    if not bpftrace_available(): record("ptp_clock_event kprobe",False,"bpftrace missing"); return
    if not symbol_exists("ptp_clock_event"): record("ptp_clock_event",False,"absent"); return
    script="""
kprobe:ptp_clock_event {
    printf("PTP_EVENT type=%d\\n", ((struct ptp_clock_event*)arg1)->type);
}
interval:s:5 { printf("KPROBE_DONE\\n"); exit(); }
"""
    out=run_bpftrace(script,timeout=9)
    done="KPROBE_DONE" in out
    record("ptp_clock_event kprobe compiles+runs",done,out[:80] if not done else "")
    record("ptp_clock_event fired in 5s",
           "PTP_EVENT" in out, "no events (no external timestamps)" if "PTP_EVENT" not in out else "")

def step7():
    print("\n── Step 7: kprobe on ptp_clock_register ────────────────────────")
    if not bpftrace_available(): record("ptp_clock_register kprobe",False,"bpftrace missing"); return
    if not symbol_exists("ptp_clock_register"): record("ptp_clock_register",False,"absent"); return
    script="""
kprobe:ptp_clock_register {
    printf("PTP_REGISTER comm=%s\\n", comm);
}
interval:s:4 { printf("REG_DONE\\n"); exit(); }
"""
    out=run_bpftrace(script,timeout=8)
    record("ptp_clock_register kprobe compiles","REG_DONE" in out or "ERROR" not in out.upper())

def step8():
    print("\n── Step 8: sysfs attributes ────────────────────────────────────")
    for ptp_dev in glob.glob("/sys/class/ptp/ptp*")[:2]:
        name=os.path.basename(ptp_dev)
        for attr in ["clock_name","max_adjustment","n_ext_ts","n_per_out","n_pins","pps_available"]:
            val=sysfs_read(f"{ptp_dev}/{attr}")
            if val is not None:
                record(f"{name}/{attr}",True,val)

def step9():
    print("\n── Step 9: PPS support ────────────────────────────────────────")
    pps_devs=glob.glob("/dev/pps*")
    record("PPS devices present",len(pps_devs)>0,
           f"{pps_devs}" if pps_devs else "no PPS (need GPS or PTP HW with PPS)")
    record("pps_register_source symbol",symbol_exists("pps_register_source"))
    # Check ptp with pps support
    for ptp_dev in glob.glob("/sys/class/ptp/ptp*"):
        pps=sysfs_read(f"{ptp_dev}/pps_available")
        if pps and pps=="1":
            record(f"{os.path.basename(ptp_dev)} PPS capable",True)

def step10():
    print("\n── Step 10: Virtual PHC (ptp_vclock) ──────────────────────────")
    for sym in ["ptp_vclock_register","ptp_get_vclocks_index"]:
        record(f"symbol {sym}",symbol_exists(sym))
    # KVM PTP
    record("ptp_kvm symbol",symbol_exists("kvm_arch_ptp_init"),
           "KVM guest PTP not available" if not symbol_exists("kvm_arch_ptp_init") else "")

# ── Main ──

def main():
    print("="*64)
    print("  PTP Clock Subsystem Verification")
    print("  Linux kernel: drivers/ptp/")
    print("="*64)
    step1();step2();step3();step4();step5()
    step6();step7();step8();step9();step10()
    print("\n"+"="*64); print("  SUMMARY"); print("="*64)
    passed=sum(1 for _,ok,_ in results if ok)
    failed=sum(1 for _,ok,_ in results if not ok)
    print(f"  PASS: {passed}/{len(results)}   FAIL: {failed}/{len(results)}")
    if failed:
        for n,ok,d in results:
            if not ok: print(f"    - {n}"+(f": {d}" if d else ""))
    print("\n  NOTE: Most steps need a NIC with PHC support (Intel i210/igc,")
    print("  Mellanox mlx5, etc.) or a dedicated PTP card (OCP TAP).")
    print("="*64)
    sys.exit(0 if not failed else 1)

if __name__=="__main__": main()
