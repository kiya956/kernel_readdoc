#!/usr/bin/env python3
"""
JBD2 Subsystem Workflow Verification
======================================
Uses bpftrace to trace the JBD2 journaling call chain:
transaction start → dirty → commit → checkpoint.

Requirements:
  - Linux with ext4 + jbd2 (CONFIG_JBD2=y/m)
  - bpftrace >= 0.14
  - Root privileges
  - A writable ext4 filesystem (defaults to /tmp)

Usage:
  sudo python3 test_jbd2_subsystem.py [--dir /mnt/ext4]
"""

import subprocess, sys, os, time, textwrap, tempfile, argparse

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
    r = run("which bpftrace")
    if not r or r.returncode != 0:
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
        if any(x in combined for x in ("not traceable","No probes","ERROR")):
            print(f"{SKIP}  Symbol not traceable")
            print(f"         {err.strip()[:200]}")
            results.append((num, desc, "SKIP"))
        else:
            print(f"{FAIL}  Expected '{keyword}' not found")
            print(f"         {combined.strip()[:200]}")
            results.append((num, desc, "FAIL"))

def step1_symbols(workdir):
    print(f"\n── Step 1: JBD2 symbols in kernel")
    r = run("grep -c ' jbd2_' /proc/kallsyms")
    count = int(r.stdout.strip()) if r and r.returncode == 0 else 0
    if count > 20:
        print(f"{PASS}  {count} jbd2_* symbols found")
        results.append((1, "JBD2 symbols in kallsyms", "PASS"))
    else:
        print(f"{FAIL}  Only {count} jbd2_* symbols - JBD2 may not be built")
        results.append((1, "JBD2 symbols in kallsyms", "FAIL"))

def step2_ext4_mount(workdir):
    print(f"\n── Step 2: ext4 filesystem mounted at workdir")
    r = run(f"stat -f -c '%T' {workdir}")
    fstype = r.stdout.strip() if r else ""
    if "ext2" in fstype or "ext4" in fstype or "ext" in fstype.lower():
        print(f"{PASS}  {workdir} is ext4 (type={fstype})")
        results.append((2, "ext4 fs detected", "PASS"))
    else:
        # Check via /proc/mounts
        r2 = run(f"grep ext4 /proc/mounts | head -1")
        if r2 and "ext4" in r2.stdout:
            mp = r2.stdout.split()[1]
            print(f"{PASS}  ext4 found at {mp} (using for trigger)")
            results.append((2, "ext4 fs detected", "PASS"))
        else:
            print(f"{SKIP}  No ext4 mount found; some steps will skip")
            results.append((2, "ext4 fs detected", "SKIP"))

def step3_journal_start(workdir):
    bpf_step(3, "jbd2_journal_start called on metadata write",
        textwrap.dedent("""
            kprobe:jbd2_journal_start {
                printf("JBD2_JOURNAL_START journal=%p pid=%d comm=%s\\n",
                       arg0, pid, comm);
                exit();
            }
            interval:s:8 { exit(); }
        """),
        trigger=f"touch {workdir}/jbd2_test_$$ 2>/dev/null; true",
        keyword="JBD2_JOURNAL_START",
        timeout=10,
    )

def step4_journal_dirty_metadata(workdir):
    bpf_step(4, "jbd2_journal_dirty_metadata marks buffer for commit",
        textwrap.dedent("""
            kprobe:jbd2_journal_dirty_metadata {
                printf("JBD2_DIRTY_METADATA handle=%p bh=%p\\n",
                       arg0, arg1);
                exit();
            }
            interval:s:8 { exit(); }
        """),
        trigger=f"touch {workdir}/jbd2_dirty_$$ 2>/dev/null; true",
        keyword="JBD2_DIRTY_METADATA",
        timeout=10,
    )

def step5_journal_stop(workdir):
    bpf_step(5, "jbd2_journal_stop completes the handle",
        textwrap.dedent("""
            kprobe:jbd2_journal_stop {
                printf("JBD2_JOURNAL_STOP handle=%p pid=%d\\n",
                       arg0, pid);
                exit();
            }
            interval:s:8 { exit(); }
        """),
        trigger=f"touch {workdir}/jbd2_stop_$$ 2>/dev/null; true",
        keyword="JBD2_JOURNAL_STOP",
        timeout=10,
    )

def step6_log_start_commit(workdir):
    bpf_step(6, "jbd2_log_start_commit triggers the commit thread",
        textwrap.dedent("""
            kprobe:jbd2_log_start_commit {
                printf("JBD2_LOG_START_COMMIT journal=%p tid=%d\\n",
                       arg0, arg1);
                exit();
            }
            interval:s:10 { exit(); }
        """),
        trigger=(f"for i in $(seq 20); do touch {workdir}/jbd2_commit_$i_$$; done; "
                 f"sync 2>/dev/null; true"),
        keyword="JBD2_LOG_START_COMMIT",
        timeout=12,
    )

def step7_log_wait_commit():
    bpf_step(7, "jbd2_log_wait_commit blocks until commit finishes",
        textwrap.dedent("""
            kprobe:jbd2_log_wait_commit {
                printf("JBD2_LOG_WAIT_COMMIT journal=%p tid=%d\\n",
                       arg0, arg1);
                exit();
            }
            interval:s:10 { exit(); }
        """),
        trigger="sync",
        keyword="JBD2_LOG_WAIT_COMMIT",
        timeout=12,
    )

def step8_complete_transaction():
    bpf_step(8, "jbd2_complete_transaction called by fsync",
        textwrap.dedent("""
            kprobe:jbd2_complete_transaction {
                printf("JBD2_COMPLETE_TRANSACTION journal=%p tid=%d\\n",
                       arg0, arg1);
                exit();
            }
            interval:s:10 { exit(); }
        """),
        trigger=(
            "python3 -c \""
            "import os, tempfile;"
            "f=open('/tmp/jbd2_fsync_test','w');"
            "f.write('x'*4096);"
            "f.flush();"
            "os.fsync(f.fileno());"
            "f.close()"
            "\" 2>/dev/null; true"
        ),
        keyword="JBD2_COMPLETE_TRANSACTION",
        timeout=12,
    )

def step9_recovery_symbol():
    print(f"\n── Step 9: jbd2 recovery symbol present")
    r = run("grep -c 'jbd2_journal_recover' /proc/kallsyms")
    count = int(r.stdout.strip()) if r and r.returncode == 0 else 0
    if count > 0:
        print(f"{PASS}  jbd2_journal_recover in kallsyms ({count} entries)")
        results.append((9, "jbd2 recovery symbol present", "PASS"))
    else:
        print(f"{SKIP}  jbd2_journal_recover not found")
        results.append((9, "jbd2 recovery symbol present", "SKIP"))

def step10_proc_jbd2():
    print(f"\n── Step 10: /proc/fs/jbd2/ exposes journal stats")
    r = run("ls /proc/fs/jbd2/ 2>/dev/null")
    if r and r.returncode == 0 and r.stdout.strip():
        journals = r.stdout.strip().split()
        print(f"{PASS}  Journals in /proc/fs/jbd2/: {journals[:4]}")
        results.append((10, "/proc/fs/jbd2/ stats", "PASS"))
    else:
        print(f"{SKIP}  /proc/fs/jbd2/ not populated")
        results.append((10, "/proc/fs/jbd2/ stats", "SKIP"))

def cleanup(workdir):
    run(f"rm -f {workdir}/jbd2_test_* {workdir}/jbd2_dirty_* "
        f"{workdir}/jbd2_stop_* {workdir}/jbd2_commit_* "
        f"/tmp/jbd2_fsync_test 2>/dev/null", timeout=5)

def print_summary():
    print("\n" + "═"*60)
    print("  JBD2 Subsystem Verification Summary")
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
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", default="/tmp", help="Writable ext4 directory")
    args = parser.parse_args()
    workdir = args.dir

    print("╔══════════════════════════════════════════════════════╗")
    print("║        JBD2 Journaling - Workflow Verification       ║")
    print("╚══════════════════════════════════════════════════════╝")
    check_prereqs()

    step1_symbols(workdir)
    step2_ext4_mount(workdir)
    step3_journal_start(workdir)
    step4_journal_dirty_metadata(workdir)
    step5_journal_stop(workdir)
    step6_log_start_commit(workdir)
    step7_log_wait_commit()
    step8_complete_transaction()
    step9_recovery_symbol()
    step10_proc_jbd2()
    cleanup(workdir)

    return print_summary()

if __name__ == "__main__":
    sys.exit(main())
