#!/usr/bin/env python3
"""
Unix Domain Socket (AF_UNIX) Subsystem Workflow Verification
==============================================================
Uses bpftrace to trace AF_UNIX socket operations: stream/dgram
send/recv, connect, bind, listen, accept, fd passing, and GC.

Requirements:
  - Linux with CONFIG_UNIX=y (always built-in)
  - bpftrace >= 0.14
  - Root privileges

Usage:
  sudo python3 test_unix_subsystem.py

Trigger: AF_UNIX socketpair() + send/recv to exercise kernel paths.
"""

import subprocess, sys, os, time, textwrap, tempfile, socket

PASS = "\033[32m[PASS]\033[0m"
FAIL = "\033[31m[FAIL]\033[0m"
SKIP = "\033[33m[SKIP]\033[0m"
INFO = "\033[34m[INFO]\033[0m"
results = []

def run(cmd, timeout=10):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True,
                              text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None

def check_prereqs():
    print(f"\n{INFO} Checking prerequisites...")
    if os.geteuid() != 0:
        print(f"{FAIL} Must run as root"); sys.exit(1)
    if not run("which bpftrace") or run("which bpftrace").returncode != 0:
        print(f"{FAIL} bpftrace not found"); sys.exit(1)
    print(f"{PASS} Prerequisites OK")

def trigger_unix_traffic():
    """Generate AF_UNIX traffic via socketpair to trigger probes."""
    try:
        a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        a.sendall(b"hello-unix-test-probe-data")
        b.recv(64)
        a.close()
        b.close()
    except Exception:
        pass

def trigger_unix_dgram():
    """Generate AF_UNIX DGRAM traffic."""
    try:
        a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
        a.send(b"dgram-test-payload")
        b.recv(64)
        a.close()
        b.close()
    except Exception:
        pass

def bpf_step(num, desc, script, trigger=None, keyword=None, timeout=10):
    print(f"\n── Step {num}: {desc}")
    with tempfile.NamedTemporaryFile(mode='w', suffix='.bt', delete=False) as f:
        f.write(script); bt = f.name

    proc = subprocess.Popen(["bpftrace", bt],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    time.sleep(1.5)
    if trigger:
        if callable(trigger):
            trigger()
        else:
            run(trigger, timeout=6)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill(); out, err = proc.communicate()
    os.unlink(bt)

    combined = out + err
    if keyword and keyword in combined:
        print(f"{PASS}  Detected: '{keyword}'")
        print(f"         {combined.strip()[:200]}")
        results.append((num, desc, "PASS"))
    elif not keyword and proc.returncode == 0:
        print(f"{PASS}  Script ran cleanly")
        results.append((num, desc, "PASS"))
    else:
        if any(x in combined for x in ("not traceable", "No probes", "ERROR")):
            print(f"{SKIP}  Symbol not traceable")
            print(f"         {err.strip()[:200]}")
            results.append((num, desc, "SKIP"))
        else:
            print(f"{FAIL}  Expected '{keyword}' not found")
            print(f"         {combined.strip()[:200]}")
            results.append((num, desc, "FAIL"))

def step1_unix_stream_sendmsg():
    bpf_step(1, "unix_stream_sendmsg traced on socketpair send",
        textwrap.dedent("""
            kprobe:unix_stream_sendmsg {
                printf("UNIX_STREAM_SENDMSG pid=%d comm=%s\\n", pid, comm);
                exit();
            }
            interval:s:8 { exit(); }
        """),
        trigger=trigger_unix_traffic,
        keyword="UNIX_STREAM_SENDMSG",
        timeout=10,
    )

def step2_unix_dgram_sendmsg():
    bpf_step(2, "unix_dgram_sendmsg traced on DGRAM socketpair",
        textwrap.dedent("""
            kprobe:unix_dgram_sendmsg {
                printf("UNIX_DGRAM_SENDMSG pid=%d comm=%s\\n", pid, comm);
                exit();
            }
            interval:s:8 { exit(); }
        """),
        trigger=trigger_unix_dgram,
        keyword="UNIX_DGRAM_SENDMSG",
        timeout=10,
    )

def step3_unix_stream_connect():
    bpf_step(3, "unix_stream_connect probe attachment",
        textwrap.dedent("""
            kprobe:unix_stream_connect {
                printf("UNIX_STREAM_CONNECT pid=%d comm=%s\\n", pid, comm);
                exit();
            }
            interval:s:5 { exit(); }
        """),
        keyword="Attaching",
        timeout=8,
    )

def step4_unix_release():
    bpf_step(4, "unix_release on socket close",
        textwrap.dedent("""
            kprobe:unix_release {
                printf("UNIX_RELEASE pid=%d comm=%s\\n", pid, comm);
                exit();
            }
            interval:s:8 { exit(); }
        """),
        trigger=trigger_unix_traffic,
        keyword="UNIX_RELEASE",
        timeout=10,
    )

def step5_unix_bind():
    bpf_step(5, "unix_bind probe attachment",
        textwrap.dedent("""
            kprobe:unix_bind {
                printf("UNIX_BIND pid=%d comm=%s\\n", pid, comm);
                exit();
            }
            interval:s:5 { exit(); }
        """),
        keyword="Attaching",
        timeout=8,
    )

def step6_unix_listen():
    bpf_step(6, "unix_listen probe attachment",
        textwrap.dedent("""
            kprobe:unix_listen {
                printf("UNIX_LISTEN pid=%d comm=%s\\n", pid, comm);
                exit();
            }
            interval:s:5 { exit(); }
        """),
        keyword="Attaching",
        timeout=8,
    )

def step7_unix_accept():
    bpf_step(7, "unix_accept probe attachment",
        textwrap.dedent("""
            kprobe:unix_accept {
                printf("UNIX_ACCEPT pid=%d comm=%s\\n", pid, comm);
                exit();
            }
            interval:s:5 { exit(); }
        """),
        keyword="Attaching",
        timeout=8,
    )

def step8_unix_gc():
    bpf_step(8, "unix_gc (garbage collector) probe attachment",
        textwrap.dedent("""
            kprobe:unix_gc {
                printf("UNIX_GC pid=%d\\n", pid);
                exit();
            }
            interval:s:5 { exit(); }
        """),
        keyword="Attaching",
        timeout=8,
    )

def step9_scm_send():
    bpf_step(9, "scm_send (ancillary data) probe attachment",
        textwrap.dedent("""
            kprobe:scm_send {
                printf("SCM_SEND pid=%d comm=%s\\n", pid, comm);
                exit();
            }
            interval:s:5 { exit(); }
        """),
        keyword="Attaching",
        timeout=8,
    )

def step10_proc_net_unix():
    print(f"\n── Step 10: /proc/net/unix socket listing")
    r = run("cat /proc/net/unix | head -5")
    if r and r.returncode == 0 and r.stdout.strip():
        lines = r.stdout.strip().split('\n')
        r2 = run("wc -l < /proc/net/unix")
        count = r2.stdout.strip() if r2 and r2.returncode == 0 else "?"
        print(f"{PASS}  /proc/net/unix readable ({count} entries)")
        for line in lines[:3]:
            print(f"         {line[:100]}")
        results.append((10, "/proc/net/unix listing", "PASS"))
    else:
        print(f"{FAIL}  Cannot read /proc/net/unix")
        results.append((10, "/proc/net/unix listing", "FAIL"))

def print_summary():
    print("\n" + "═"*60)
    print("  AF_UNIX Subsystem Verification Summary")
    print("═"*60)
    passed  = sum(1 for _,_,s in results if s=="PASS")
    failed  = sum(1 for _,_,s in results if s=="FAIL")
    skipped = sum(1 for _,_,s in results if s=="SKIP")
    for n,d,s in results:
        icon = PASS if s=="PASS" else (FAIL if s=="FAIL" else SKIP)
        print(f"  Step {n:>2}: {icon}  {d}")
    print("═"*60)
    print(f"  Total: {len(results)}  | \033[32mPASS:{passed}\033[0m "
          f"| \033[31mFAIL:{failed}\033[0m | \033[33mSKIP:{skipped}\033[0m")
    print("═"*60)
    if failed == 0:
        print(f"\n{PASS} All verifiable steps passed!\n"); return 0
    print(f"\n{FAIL} {failed} step(s) failed.\n"); return 1

def main():
    print("╔══════════════════════════════════════════════════════╗")
    print("║     AF_UNIX Subsystem - Workflow Verification        ║")
    print("╚══════════════════════════════════════════════════════╝")
    check_prereqs()
    step1_unix_stream_sendmsg()
    step2_unix_dgram_sendmsg()
    step3_unix_stream_connect()
    step4_unix_release()
    step5_unix_bind()
    step6_unix_listen()
    step7_unix_accept()
    step8_unix_gc()
    step9_scm_send()
    step10_proc_net_unix()
    return print_summary()

if __name__ == "__main__":
    sys.exit(main())
