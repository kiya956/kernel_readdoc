#!/usr/bin/env python3
"""
remoteproc subsystem verification via bpftrace + sysfs.

Tests:
  1. Prerequisites (bpftrace, remoteproc class)
  2. remoteproc devices enumerated
  3. state / firmware / name sysfs attrs
  4. kprobe on rproc_boot
  5. kprobe on rproc_shutdown
  6. rproc_boot latency histogram
  7. debugfs trace buffer
  8. resource_table debugfs
  9. Recovery sysfs attribute
 10. Platform driver symbols (mtk_scp, qcom_q6v5, omap_rproc)

Each step is marked PASS or FAIL.
"""

import subprocess, tempfile, os, sys, re, glob

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

def kprobe_compiles(sym,t=6):
    s=f"kprobe:{sym}{{@c++;}}interval:s:{t-1}{{printf(\"DONE\\n\");exit();}}"
    return "DONE" in run_bpftrace(s,timeout=t+2)

def get_rproc_devices(): return glob.glob("/sys/class/remoteproc/remoteproc*")

def step1():
    print("\n── Step 1: Prerequisites ──────────────────────────────────────")
    record("running as root",check_root())
    record("bpftrace available",bpftrace_available())
    record("remoteproc class",os.path.isdir("/sys/class/remoteproc"),"/sys/class/remoteproc")
    record("rproc_boot symbol",symbol_exists("rproc_boot"))

def step2():
    print("\n── Step 2: remoteproc devices ──────────────────────────────────")
    devs=get_rproc_devices()
    record("remoteproc devices present",len(devs)>0,
           f"{[os.path.basename(d) for d in devs]}" if devs else "no remote processors")
    return devs

def step3(devs):
    print("\n── Step 3: sysfs attributes ─────────────────────────────────────")
    if not devs: record("sysfs attrs",False,"no devices"); return
    for d in devs[:2]:
        n=os.path.basename(d)
        for attr in ["state","firmware","name","coredump","recovery"]:
            v=sysfs_read(f"{d}/{attr}")
            if v: record(f"{n}/{attr}",True,v)

def step4():
    print("\n── Step 4: kprobe rproc_boot ────────────────────────────────────")
    if not bpftrace_available(): record("rproc_boot kprobe",False,"bpftrace missing"); return
    if not symbol_exists("rproc_boot"): record("rproc_boot",False,"absent"); return
    ok=kprobe_compiles("rproc_boot")
    record("rproc_boot kprobe compiles",ok)

def step5():
    print("\n── Step 5: kprobe rproc_shutdown ───────────────────────────────")
    if not bpftrace_available(): record("rproc_shutdown kprobe",False,"bpftrace missing"); return
    if not symbol_exists("rproc_shutdown"): record("rproc_shutdown",False,"absent"); return
    ok=kprobe_compiles("rproc_shutdown")
    record("rproc_shutdown kprobe compiles",ok)

def step6():
    print("\n── Step 6: rproc_boot latency histogram ────────────────────────")
    if not bpftrace_available(): record("rproc_boot latency",False,"bpftrace missing"); return
    if not symbol_exists("rproc_boot"): record("rproc_boot",False,"absent"); return
    script="""
kprobe:rproc_boot    { @s[tid]=nsecs; }
kretprobe:rproc_boot {
    if(@s[tid]){ @lat_ms=hist((nsecs-@s[tid])/1000000); delete(@s[tid]); }
}
interval:s:6 { print(@lat_ms); printf("LAT_DONE\\n"); exit(); }
"""
    out=run_bpftrace(script,timeout=10)
    record("rproc_boot latency kprobe ran","LAT_DONE" in out)

def step7():
    print("\n── Step 7: debugfs trace buffer ────────────────────────────────")
    trace_dirs=glob.glob("/sys/kernel/debug/remoteproc/*/")
    record("remoteproc debugfs dirs",len(trace_dirs)>0,
           f"{[os.path.basename(d.rstrip('/')) for d in trace_dirs]}" if trace_dirs else "no debugfs")
    for d in trace_dirs[:1]:
        for f in ["trace0","resource_table","carveout_memories"]:
            if os.path.exists(f"{d}{f}"):
                v=sysfs_read(f"{d}{f}")
                record(f"  {f} readable",v is not None,
                       f"{len(v or '')} bytes" if v else "empty")

def step8():
    print("\n── Step 8: resource_table debugfs ─────────────────────────────")
    for path in glob.glob("/sys/kernel/debug/remoteproc/*/resource_table"):
        v=sysfs_read(path)
        record("resource_table readable",v is not None,
               f"{len(v or '')} bytes" if v else "absent")
        return
    record("resource_table",False,"no remoteproc in debugfs")

def step9():
    print("\n── Step 9: Recovery and coredump attrs ─────────────────────────")
    for d in get_rproc_devices()[:2]:
        n=os.path.basename(d)
        v=sysfs_read(f"{d}/recovery")
        record(f"{n}/recovery",v is not None,v or "absent")

def step10():
    print("\n── Step 10: Platform driver symbols ────────────────────────────")
    for sym,desc in [("mtk_scp_probe","MediaTek SCP"),
                     ("qcom_q6v5_wcss_probe","Qualcomm Q6"),
                     ("omap_rproc_probe","OMAP DSP"),
                     ("pru_rproc_probe","TI PRU"),
                     ("imx_dsp_rproc_probe","NXP i.MX")]:
        record(f"{desc} ({sym})",symbol_exists(sym),
               "not loaded" if not symbol_exists(sym) else "")

def main():
    print("="*64)
    print("  remoteproc Subsystem Verification")
    print("  Linux kernel: drivers/remoteproc/")
    print("="*64)
    step1(); devs=step2(); step3(devs); step4(); step5()
    step6(); step7(); step8(); step9(); step10()
    print("\n"+"="*64); print("  SUMMARY"); print("="*64)
    passed=sum(1 for _,ok,_ in results if ok)
    failed=sum(1 for _,ok,_ in results if not ok)
    print(f"  PASS: {passed}/{len(results)}   FAIL: {failed}/{len(results)}")
    if failed:
        for n,ok,d in results:
            if not ok: print(f"    - {n}"+(f": {d}" if d else ""))
    print("\n  NOTE: Device steps require a SoC with DSP/MCU (TI, Qualcomm,")
    print("  NXP, MediaTek, STM32) and CONFIG_REMOTEPROC=y.")
    print("="*64)
    sys.exit(0 if not failed else 1)

if __name__=="__main__": main()
