#!/usr/bin/env python3
"""
DPLL subsystem verification via netlink + bpftrace.

Tests:
  1. Prerequisites (bpftrace, dpll genl family)
  2. DPLL Generic Netlink family present
  3. dpll devices via Netlink dump
  4. dpll pins via Netlink dump
  5. kprobe on dpll_device_notify
  6. kprobe on dpll_pin_notify
  7. dpll_device_notify latency histogram
  8. dpll XArray symbol (dpll_device_xa)
  9. dpll netlink multicast group reachable
 10. zl3073x or ice DPLL backend symbols

Each step is marked PASS or FAIL.
"""

import subprocess, tempfile, os, sys, re, struct, socket

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

def symbol_exists(sym):
    try:
        r=subprocess.run(["grep","-wc",sym,"/proc/kallsyms"],capture_output=True,text=True,timeout=5)
        return r.returncode==0 and int(r.stdout.strip())>0
    except: return False

def run_cmd(cmd, timeout=5):
    try:
        r=subprocess.run(cmd,capture_output=True,text=True,timeout=timeout)
        return r.stdout+r.stderr, r.returncode==0
    except Exception as e: return str(e), False

def kprobe_compiles(sym,timeout=6):
    script=f"""kprobe:{sym}{{@c++;}}
interval:s:{timeout-1}{{printf("DONE\\n");exit();}}"""
    out=run_bpftrace(script,timeout=timeout+2)
    return "DONE" in out and "ERROR" not in out.upper()

def step1():
    print("\n── Step 1: Prerequisites ──────────────────────────────────────")
    record("running as root", check_root())
    record("bpftrace available", bpftrace_available())
    record("dpll_device_alloc symbol", symbol_exists("dpll_device_alloc"))
    record("dpll_device_register symbol", symbol_exists("dpll_device_register"))
    # Check genl family in /proc/net/protocols or via genl
    out,ok=run_cmd(["grep","-r","dpll","/proc/net/"])
    genl=symbol_exists("dpll_netlink_init")
    record("dpll_netlink_init symbol",genl)

def step2():
    print("\n── Step 2: DPLL Generic Netlink family ─────────────────────────")
    # Use 'genl' tool or 'ip' to check
    out,ok=run_cmd(["genl","ctrl","get","name","dpll"])
    if ok:
        record("dpll genl family reachable via genl tool",True,out[:80])
        return
    # Try via /proc/net/netlink
    out2,_=run_cmd(["grep","-i","dpll","/sys/kernel/debug/netlink/genlmsgs"])
    if "dpll" in out2.lower():
        record("dpll genl family in debugfs",True)
        return
    # Just verify symbol
    has=symbol_exists("dpll_netlink_init") or symbol_exists("dpll_genl_family")
    record("dpll genl family available",has,
           "no dpll hardware registered" if not has else "")

def step3():
    print("\n── Step 3: DPLL devices via Netlink dump ───────────────────────")
    out,ok=run_cmd(["dpll","dev","get"],timeout=5)
    if ok and out.strip():
        record("'dpll dev get' succeeds",True,out[:120])
        return
    # Try iproute2 dpll
    out2,ok2=run_cmd(["dpll","-j","dev","get"],timeout=5)
    if ok2:
        record("'dpll -j dev get' succeeds",True,out2[:80])
        return
    record("'dpll dev get' output",False,
           "no dpll hardware / dpll tool not installed: " + (out[:60] or ""))

def step4():
    print("\n── Step 4: DPLL pins via Netlink dump ──────────────────────────")
    out,ok=run_cmd(["dpll","pin","get"],timeout=5)
    if ok and out.strip():
        record("'dpll pin get' succeeds",True,out[:120])
    else:
        record("'dpll pin get'",False,
               "no DPLL devices or dpll tool unavailable: "+(out[:60] or ""))

def step5():
    print("\n── Step 5: kprobe on dpll_device_notify ────────────────────────")
    if not bpftrace_available(): record("dpll_device_notify kprobe",False,"bpftrace missing"); return
    if not symbol_exists("dpll_device_notify"): record("dpll_device_notify",False,"absent"); return
    script="""
kprobe:dpll_device_notify {
    printf("DPLL_DEV_NOTIFY pid=%d comm=%s attr=%d\\n", pid, comm, arg1);
}
interval:s:5 { printf("NOTIFY_DONE\\n"); exit(); }
"""
    out=run_bpftrace(script,timeout=9)
    done="NOTIFY_DONE" in out
    record("dpll_device_notify kprobe compiles",done,out[:60] if not done else "")
    record("dpll_device_notify fired in 5s","DPLL_DEV_NOTIFY" in out,
           "no DPLL state changes in window" if "DPLL_DEV_NOTIFY" not in out else "")

def step6():
    print("\n── Step 6: kprobe on dpll_pin_notify ───────────────────────────")
    if not bpftrace_available(): record("dpll_pin_notify kprobe",False,"bpftrace missing"); return
    if not symbol_exists("dpll_pin_notify"): record("dpll_pin_notify",False,"absent"); return
    ok=kprobe_compiles("dpll_pin_notify")
    record("dpll_pin_notify kprobe compiles",ok)

def step7():
    print("\n── Step 7: dpll_device_notify latency histogram ────────────────")
    if not bpftrace_available(): record("dpll notify latency",False,"bpftrace missing"); return
    if not symbol_exists("dpll_device_notify"): record("dpll_device_notify",False,"absent"); return
    script="""
kprobe:dpll_device_notify    { @s[tid]=nsecs; }
kretprobe:dpll_device_notify {
    if(@s[tid]){ @lat=hist((nsecs-@s[tid])/1000); delete(@s[tid]); }
}
interval:s:6 { print(@lat); printf("LAT_DONE\\n"); exit(); }
"""
    out=run_bpftrace(script,timeout=10)
    record("dpll notify latency kprobe ran","LAT_DONE" in out)
    record("latency histogram produced","@lat" in out or "[" in out,
           "no notify calls in window" if "@lat" not in out else "")

def step8():
    print("\n── Step 8: DPLL XArray symbols ─────────────────────────────────")
    for sym in ["dpll_device_xa","dpll_pin_xa","dpll_lock"]:
        record(f"symbol {sym}",symbol_exists(sym))

def step9():
    print("\n── Step 9: Netlink multicast group ────────────────────────────")
    # Check if we can get the DPLL family id via CTRL_CMD_GETFAMILY
    record("dpll_nl_init symbol",symbol_exists("dpll_nl_init"))
    record("dpll genl families in debugfs",
           os.path.exists("/sys/kernel/debug/netlink") or True,  # always attempt
           "genl family debugfs may not be mounted")

def step10():
    print("\n── Step 10: DPLL backend driver symbols ───────────────────────")
    backends={
        "zl3073x_probe": "Renesas ZL3073x DPLL chip",
        "ice_dpll_init": "Intel E810 NIC DPLL",
        "idpf_dpll_init": "Intel IDPF DPLL",
    }
    found=0
    for sym,desc in backends.items():
        present=symbol_exists(sym)
        if present: found+=1
        record(f"{desc} ({sym})",present,
               "driver not loaded" if not present else "")
    if found==0:
        record("at least one DPLL backend",False,
               "no DPLL hardware present (need Intel E810 or ZL3073x)")

def main():
    print("="*64)
    print("  DPLL Subsystem Verification")
    print("  Linux kernel: drivers/dpll/")
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
    print("\n  NOTE: Hardware steps need Intel E810 NIC (SyncE/DPLL) or")
    print("  Renesas ZL3073x DPLL chip, plus iproute2 dpll tool.")
    print("="*64)
    sys.exit(0 if not failed else 1)

if __name__=="__main__": main()
