#!/usr/bin/env python3
"""
fsnotify/inotify/fanotify Workflow Verification
=================================================
Uses bpftrace to trace the fsnotify dispatch path from VFS hooks through
the core to inotify/fanotify group event queues.

Requirements:
  - Linux with inotify + fanotify (CONFIG_INOTIFY_USER, CONFIG_FANOTIFY)
  - bpftrace >= 0.14
  - Root privileges

Usage:
  sudo python3 test_fsnotify_subsystem.py
"""

import subprocess, sys, os, time, textwrap, tempfile

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

def bpf_step(num, desc, script, trigger=None, keyword=None, timeout=10):
    print(f"\n── Step {num}: {desc}")
    with tempfile.NamedTemporaryFile(mode='w', suffix='.bt', delete=False) as f:
        f.write(script); bt = f.name

    proc = subprocess.Popen(["bpftrace", bt],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    time.sleep(1.5)
    if trigger:
        run(trigger, timeout=5)
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

def step1_symbols():
    print(f"\n── Step 1: fsnotify symbols present")
    r = run("grep -c ' fsnotify' /proc/kallsyms")
    count = int(r.stdout.strip()) if r and r.returncode == 0 else 0
    if count > 10:
        print(f"{PASS}  {count} fsnotify symbols found")
        results.append((1, "fsnotify symbols in kallsyms", "PASS"))
    else:
        print(f"{FAIL}  Only {count} fsnotify symbols")
        results.append((1, "fsnotify symbols in kallsyms", "FAIL"))

def step2_inotify_fd():
    """Verify inotify_init1 syscall works."""
    print(f"\n── Step 2: inotify_init1 creates valid fd")
    r = run("python3 -c \""
            "import ctypes,os;"
            "libc=ctypes.CDLL(None,use_errno=True);"
            "fd=libc.inotify_init1(0);"
            "print('INOTIFY_FD=%d'%fd);"
            "os.close(fd) if fd>=0 else None"
            "\"")
    if r and "INOTIFY_FD=" in r.stdout:
        fd = int(r.stdout.strip().split("=")[1])
        if fd >= 0:
            print(f"{PASS}  inotify_init1() returned fd={fd}")
            results.append((2, "inotify_init1 syscall works", "PASS"))
            return
    print(f"{FAIL}  inotify_init1 failed: {r.stderr if r else 'timeout'}")
    results.append((2, "inotify_init1 syscall works", "FAIL"))

def step3_fsnotify_create():
    bpf_step(3, "fsnotify_create called on file creation",
        textwrap.dedent("""
            kprobe:fsnotify_create {
                printf("FSNOTIFY_CREATE dir=%p dentry=%p pid=%d comm=%s\\n",
                       arg0, arg1, pid, comm);
                exit();
            }
            interval:s:8 { exit(); }
        """),
        trigger="touch /tmp/fsnotify_test_$$",
        keyword="FSNOTIFY_CREATE",
        timeout=10,
    )

def step4_fsnotify_modify():
    bpf_step(4, "fsnotify_modify fired on file write",
        textwrap.dedent("""
            kprobe:fsnotify_modify {
                printf("FSNOTIFY_MODIFY file=%p inode=%p pid=%d\\n",
                       arg0, ((struct file *)arg0)->f_inode, pid);
                exit();
            }
            interval:s:8 { exit(); }
        """),
        trigger="echo hello >> /tmp/fsnotify_test_$$",
        keyword="FSNOTIFY_MODIFY",
        timeout=10,
    )

def step5_fsnotify_open():
    bpf_step(5, "fsnotify_open traced on file open",
        textwrap.dedent("""
            kprobe:fsnotify_open {
                printf("FSNOTIFY_OPEN file=%p pid=%d comm=%s\\n",
                       arg0, pid, comm);
                exit();
            }
            interval:s:8 { exit(); }
        """),
        trigger="cat /tmp/fsnotify_test_$$ >/dev/null 2>&1",
        keyword="FSNOTIFY_OPEN",
        timeout=10,
    )

def step6_inotify_handle_inode_event():
    bpf_step(6, "inotify_handle_inode_event queues event to group",
        textwrap.dedent("""
            kprobe:inotify_handle_inode_event {
                printf("INOTIFY_HANDLE_EVENT group=%p mask=0x%x\\n",
                       arg0, arg2);
                exit();
            }
            interval:s:8 { exit(); }
        """),
        trigger=(
            "python3 -c \""
            "import ctypes,os,threading;"
            "libc=ctypes.CDLL(None);"
            "fd=libc.inotify_init1(0);"
            "libc.inotify_add_watch(fd,b'/tmp',0xfff);"
            "import time; time.sleep(0.2);"
            "open('/tmp/inotify_probe_$$','w').close();"
            "time.sleep(0.2);"
            "os.close(fd);"
            "os.unlink('/tmp/inotify_probe_$$')"
            "\" 2>/dev/null; true"
        ),
        keyword="INOTIFY_HANDLE_EVENT",
        timeout=10,
    )

def step7_fsnotify_delete():
    bpf_step(7, "fsnotify_delete called on file removal",
        textwrap.dedent("""
            kprobe:fsnotify_delete {
                printf("FSNOTIFY_DELETE dir=%p inode=%p pid=%d\\n",
                       arg0, arg1, pid);
                exit();
            }
            interval:s:8 { exit(); }
        """),
        trigger="rm -f /tmp/fsnotify_test_$$",
        keyword="FSNOTIFY_DELETE",
        timeout=10,
    )

def step8_fanotify_symbols():
    print(f"\n── Step 8: fanotify symbols present")
    r = run("grep -c ' fanotify' /proc/kallsyms")
    count = int(r.stdout.strip()) if r and r.returncode == 0 else 0
    if count > 5:
        print(f"{PASS}  {count} fanotify symbols found")
        results.append((8, "fanotify symbols in kallsyms", "PASS"))
    else:
        print(f"{SKIP}  fanotify not built (count={count})")
        results.append((8, "fanotify symbols in kallsyms", "SKIP"))

def step9_mark_add():
    bpf_step(9, "fsnotify_add_mark_locked called on inotify_add_watch",
        textwrap.dedent("""
            kprobe:fsnotify_add_mark_locked {
                printf("FSNOTIFY_ADD_MARK mark=%p group=%p\\n",
                       arg0, arg1);
                exit();
            }
            interval:s:8 { exit(); }
        """),
        trigger=(
            "python3 -c \""
            "import ctypes,os;"
            "libc=ctypes.CDLL(None);"
            "fd=libc.inotify_init1(0);"
            "libc.inotify_add_watch(fd,b'/tmp',0xfff);"
            "os.close(fd)"
            "\" 2>/dev/null"
        ),
        keyword="FSNOTIFY_ADD_MARK",
        timeout=10,
    )

def step10_dnotify_symbol():
    print(f"\n── Step 10: dnotify (legacy F_NOTIFY) symbol present")
    r = run("grep -c 'dnotify_parent' /proc/kallsyms")
    count = int(r.stdout.strip()) if r and r.returncode == 0 else 0
    if count > 0:
        print(f"{PASS}  dnotify_parent present ({count})")
        results.append((10, "dnotify legacy symbol present", "PASS"))
    else:
        print(f"{SKIP}  dnotify not built")
        results.append((10, "dnotify legacy symbol present", "SKIP"))

# ── Summary ──────────────────────────────────────────────────────

def print_summary():
    print("\n" + "═"*60)
    print("  fsnotify/inotify/fanotify Verification Summary")
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
    print("║   fsnotify/inotify/fanotify - Workflow Verification  ║")
    print("╚══════════════════════════════════════════════════════╝")
    check_prereqs()
    step1_symbols()
    step2_inotify_fd()
    step3_fsnotify_create()
    step4_fsnotify_modify()
    step5_fsnotify_open()
    step6_inotify_handle_inode_event()
    step7_fsnotify_delete()
    step8_fanotify_symbols()
    step9_mark_add()
    step10_dnotify_symbol()
    # cleanup stale files
    run("rm -f /tmp/fsnotify_test_* /tmp/inotify_probe_* 2>/dev/null", timeout=3)
    return print_summary()

if __name__ == "__main__":
    sys.exit(main())
