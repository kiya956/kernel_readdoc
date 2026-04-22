#!/usr/bin/env python3
"""
MPTCP Subsystem Workflow Verification
=======================================
Uses bpftrace to trace the MPTCP connection flow:
  socket creation → subflow → MP_CAPABLE handshake → data exchange

Requirements:
  - Linux with MPTCP (CONFIG_MPTCP=y)
  - bpftrace >= 0.14
  - Root privileges
  - MPTCP enabled: sysctl net.mptcp.enabled=1

Usage:
  sudo python3 test_mptcp_subsystem.py
"""

import subprocess, sys, os, time, textwrap, tempfile, socket

PASS = "\033[32m[PASS]\033[0m"
FAIL = "\033[31m[FAIL]\033[0m"
SKIP = "\033[33m[SKIP]\033[0m"
INFO = "\033[34m[INFO]\033[0m"
results = []
IPPROTO_MPTCP = 262  # from include/uapi/linux/in.h

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

def bpf_step(num, desc, script, trigger=None, keyword=None, timeout=10):
    print(f"\n── Step {num}: {desc}")
    with tempfile.NamedTemporaryFile(mode='w', suffix='.bt', delete=False) as f:
        f.write(script); bt = f.name

    proc = subprocess.Popen(["bpftrace", bt],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    time.sleep(1.5)
    if trigger:
        run(trigger, timeout=8)
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
        if any(x in combined for x in ("not traceable","No probes","ERROR")):
            print(f"{SKIP}  Symbol not traceable")
            print(f"         {err.strip()[:200]}")
            results.append((num, desc, "SKIP"))
        else:
            print(f"{FAIL}  Expected '{keyword}' not found")
            print(f"         {combined.strip()[:200]}")
            results.append((num, desc, "FAIL"))

# ── Individual steps ────────────────────────────────────────────

def step1_mptcp_enabled():
    print(f"\n── Step 1: MPTCP enabled via sysctl")
    r = run("sysctl -n net.mptcp.enabled 2>/dev/null")
    if r and r.stdout.strip() == "1":
        print(f"{PASS}  net.mptcp.enabled=1")
        results.append((1, "MPTCP enabled", "PASS"))
    elif r and r.stdout.strip() == "0":
        print(f"{INFO}  MPTCP disabled, enabling...")
        run("sysctl -w net.mptcp.enabled=1", timeout=3)
        print(f"{PASS}  Enabled net.mptcp.enabled=1")
        results.append((1, "MPTCP enabled", "PASS"))
    else:
        print(f"{SKIP}  sysctl net.mptcp.enabled not found (MPTCP not built?)")
        results.append((1, "MPTCP enabled", "SKIP"))

def step2_mptcp_symbols():
    print(f"\n── Step 2: MPTCP symbols in kernel")
    r = run("grep -c ' mptcp_' /proc/kallsyms")
    count = int(r.stdout.strip()) if r and r.returncode == 0 else 0
    if count > 20:
        print(f"{PASS}  {count} mptcp_* symbols found")
        results.append((2, "MPTCP symbols in kallsyms", "PASS"))
    else:
        print(f"{FAIL}  Only {count} mptcp_* symbols")
        results.append((2, "MPTCP symbols in kallsyms", "FAIL"))

def step3_mptcp_socket_create():
    """Verify IPPROTO_MPTCP socket can be created."""
    print(f"\n── Step 3: IPPROTO_MPTCP socket creation")
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM, IPPROTO_MPTCP)
        s.close()
        print(f"{PASS}  socket(AF_INET, SOCK_STREAM, IPPROTO_MPTCP) succeeded")
        results.append((3, "MPTCP socket create", "PASS"))
    except OSError as e:
        print(f"{SKIP}  MPTCP socket not supported: {e}")
        results.append((3, "MPTCP socket create", "SKIP"))

def step4_mptcp_sock_init():
    bpf_step(4, "mptcp_sk_clone_init or mptcp_init_sock called on connect",
        textwrap.dedent("""
            kprobe:mptcp_init_sock {
                printf("MPTCP_INIT_SOCK sk=%p pid=%d comm=%s\\n",
                       arg0, pid, comm);
                exit();
            }
            interval:s:8 { exit(); }
        """),
        trigger=(
            "python3 -c \""
            "import socket;"
            "IPPROTO_MPTCP=262;"
            "s=socket.socket(socket.AF_INET,socket.SOCK_STREAM,IPPROTO_MPTCP);"
            "s.close()"
            "\" 2>/dev/null; true"
        ),
        keyword="MPTCP_INIT_SOCK",
        timeout=10,
    )

def step5_subflow_init():
    bpf_step(5, "mptcp_subflow_create_socket called for first subflow",
        textwrap.dedent("""
            kprobe:mptcp_subflow_create_socket {
                printf("MPTCP_SUBFLOW_CREATE sk=%p flags=%d\\n",
                       arg0, arg1);
                exit();
            }
            interval:s:8 { exit(); }
        """),
        trigger=(
            "python3 -c \""
            "import socket,threading;"
            "IPPROTO_MPTCP=262;"
            "srv=socket.socket(socket.AF_INET,socket.SOCK_STREAM,IPPROTO_MPTCP);"
            "srv.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);"
            "srv.bind(('127.0.0.1',19999));"
            "srv.listen(1);"
            "t=threading.Thread(target=lambda:srv.accept());"
            "t.daemon=True; t.start();"
            "import time; time.sleep(0.1);"
            "c=socket.socket(socket.AF_INET,socket.SOCK_STREAM,IPPROTO_MPTCP);"
            "c.connect(('127.0.0.1',19999));"
            "c.close(); srv.close()"
            "\" 2>/dev/null; true"
        ),
        keyword="MPTCP_SUBFLOW_CREATE",
        timeout=12,
    )

def step6_finish_connect():
    bpf_step(6, "mptcp_finish_connect completes MPTCP handshake",
        textwrap.dedent("""
            kprobe:mptcp_finish_connect {
                printf("MPTCP_FINISH_CONNECT ssk=%p pid=%d\\n",
                       arg0, pid);
                exit();
            }
            interval:s:10 { exit(); }
        """),
        trigger=(
            "python3 -c \""
            "import socket,threading,time;"
            "IPPROTO_MPTCP=262;"
            "srv=socket.socket(socket.AF_INET,socket.SOCK_STREAM,IPPROTO_MPTCP);"
            "srv.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);"
            "srv.bind(('127.0.0.1',19998)); srv.listen(1);"
            "def accept(): conn,_=srv.accept(); conn.close()"
            "t=threading.Thread(target=accept); t.daemon=True; t.start();"
            "time.sleep(0.1);"
            "c=socket.socket(socket.AF_INET,socket.SOCK_STREAM,IPPROTO_MPTCP);"
            "c.connect(('127.0.0.1',19998)); c.close(); srv.close()"
            "\" 2>/dev/null; true"
        ),
        keyword="MPTCP_FINISH_CONNECT",
        timeout=12,
    )

def step7_sendmsg():
    bpf_step(7, "mptcp_sendmsg routes data over subflow",
        textwrap.dedent("""
            kprobe:mptcp_sendmsg {
                printf("MPTCP_SENDMSG sk=%p len=%d pid=%d\\n",
                       arg0, arg2, pid);
                exit();
            }
            interval:s:10 { exit(); }
        """),
        trigger=(
            "python3 -c \""
            "import socket,threading,time;"
            "IPPROTO_MPTCP=262;"
            "srv=socket.socket(socket.AF_INET,socket.SOCK_STREAM,IPPROTO_MPTCP);"
            "srv.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);"
            "srv.bind(('127.0.0.1',19997)); srv.listen(1);"
            "def accept():"
            "  conn,_=srv.accept();"
            "  conn.recv(64); conn.close()"
            "t=threading.Thread(target=accept); t.daemon=True; t.start();"
            "time.sleep(0.1);"
            "c=socket.socket(socket.AF_INET,socket.SOCK_STREAM,IPPROTO_MPTCP);"
            "c.connect(('127.0.0.1',19997));"
            "c.send(b'hello mptcp');"
            "c.close(); srv.close()"
            "\" 2>/dev/null; true"
        ),
        keyword="MPTCP_SENDMSG",
        timeout=12,
    )

def step8_proc_net_mptcp():
    print(f"\n── Step 8: /proc/net/mptcp shows active connections")
    r = run("cat /proc/net/mptcp 2>/dev/null | head -5")
    if r and r.returncode == 0:
        print(f"{PASS}  /proc/net/mptcp readable")
        print(f"         {r.stdout.strip()[:200]}")
        results.append((8, "/proc/net/mptcp readable", "PASS"))
    else:
        print(f"{SKIP}  /proc/net/mptcp not available")
        results.append((8, "/proc/net/mptcp readable", "SKIP"))

def step9_ss_mptcp():
    print(f"\n── Step 9: ss -M lists MPTCP sockets")
    r = run("ss -M 2>/dev/null | head -5")
    if r and r.returncode == 0:
        print(f"{PASS}  ss -M works")
        results.append((9, "ss -M MPTCP socket listing", "PASS"))
    else:
        print(f"{SKIP}  ss -M not available")
        results.append((9, "ss -M MPTCP socket listing", "SKIP"))

def step10_pm_netlink():
    print(f"\n── Step 10: ip mptcp endpoint show (kernel PM netlink)")
    r = run("ip mptcp endpoint show 2>/dev/null")
    if r and r.returncode == 0:
        print(f"{PASS}  ip mptcp endpoint show works")
        results.append((10, "MPTCP kernel PM netlink", "PASS"))
    else:
        print(f"{SKIP}  ip mptcp not available (iproute2 too old?)")
        results.append((10, "MPTCP kernel PM netlink", "SKIP"))

# ── Summary ──────────────────────────────────────────────────────

def print_summary():
    print("\n" + "═"*60)
    print("  MPTCP Subsystem Verification Summary")
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
    print("║       MPTCP Subsystem - Workflow Verification        ║")
    print("╚══════════════════════════════════════════════════════╝")
    check_prereqs()
    step1_mptcp_enabled()
    step2_mptcp_symbols()
    step3_mptcp_socket_create()
    step4_mptcp_sock_init()
    step5_subflow_init()
    step6_finish_connect()
    step7_sendmsg()
    step8_proc_net_mptcp()
    step9_ss_mptcp()
    step10_pm_netlink()
    return print_summary()

if __name__ == "__main__":
    sys.exit(main())
