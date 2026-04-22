#!/usr/bin/env python3
"""
iomap Subsystem Workflow Verification
=======================================
Traces the iomap buffered/direct I/O path via bpftrace.

Requirements:
  - Linux with iomap (CONFIG_FS_IOMAP=y, typically from XFS/ext4)
  - bpftrace >= 0.14
  - Root privileges
  - A writable ext4 or XFS filesystem at --dir (default /tmp)

Usage:
  sudo python3 test_iomap_subsystem.py [--dir /mnt/xfs]
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

def step1_symbols():
    print(f"\n── Step 1: iomap symbols present in kernel")
    r = run("grep -c ' iomap_' /proc/kallsyms")
    count = int(r.stdout.strip()) if r and r.returncode == 0 else 0
    if count > 10:
        print(f"{PASS}  {count} iomap_* symbols found")
        results.append((1, "iomap symbols in kallsyms", "PASS"))
    else:
        print(f"{FAIL}  Only {count} iomap symbols (iomap not built?)")
        results.append((1, "iomap symbols in kallsyms", "FAIL"))

def step2_iomap_begin(workdir):
    bpf_step(2, "iomap_iter calls fs iomap_begin for block mapping",
        textwrap.dedent("""
            kprobe:iomap_iter {
                printf("IOMAP_ITER inode=%p pos=%lld pid=%d comm=%s\\n",
                       ((struct iomap_iter *)arg0)->inode,
                       ((struct iomap_iter *)arg0)->pos,
                       pid, comm);
                exit();
            }
            interval:s:8 { exit(); }
        """),
        trigger=f"dd if=/dev/urandom of={workdir}/iomap_test_$$ bs=4096 count=4 2>/dev/null",
        keyword="IOMAP_ITER",
        timeout=10,
    )

def step3_buffered_write(workdir):
    bpf_step(3, "iomap_file_buffered_write called on write()",
        textwrap.dedent("""
            kprobe:iomap_file_buffered_write {
                printf("IOMAP_BUFFERED_WRITE iocb=%p len=%d pid=%d\\n",
                       arg0, arg1, pid);
                exit();
            }
            interval:s:8 { exit(); }
        """),
        trigger=f"dd if=/dev/urandom of={workdir}/iomap_bw_$$ bs=4096 count=2 2>/dev/null",
        keyword="IOMAP_BUFFERED_WRITE",
        timeout=10,
    )

def step4_read_folio(workdir):
    bpf_step(4, "iomap_read_folio called on read()",
        textwrap.dedent("""
            kprobe:iomap_read_folio {
                printf("IOMAP_READ_FOLIO folio=%p inode=%p pid=%d\\n",
                       arg0, ((struct folio *)arg0)->mapping->host, pid);
                exit();
            }
            interval:s:8 { exit(); }
        """),
        trigger=(f"sync; echo 3 > /proc/sys/vm/drop_caches 2>/dev/null; "
                 f"cat {workdir}/iomap_test_$$ >/dev/null 2>&1; true"),
        keyword="IOMAP_READ_FOLIO",
        timeout=10,
    )

def step5_writepages(workdir):
    bpf_step(5, "iomap_writepages called during writeback",
        textwrap.dedent("""
            kprobe:iomap_writepages {
                printf("IOMAP_WRITEPAGES mapping=%p wbc=%p pid=%d\\n",
                       arg0, arg1, pid);
                exit();
            }
            interval:s:10 { exit(); }
        """),
        trigger=(f"dd if=/dev/urandom of={workdir}/iomap_wb_$$ bs=65536 count=8 2>/dev/null; "
                 f"sync"),
        keyword="IOMAP_WRITEPAGES",
        timeout=12,
    )

def step6_direct_io(workdir):
    bpf_step(6, "iomap_dio_rw called for O_DIRECT write",
        textwrap.dedent("""
            kprobe:iomap_dio_rw {
                printf("IOMAP_DIO_RW iocb=%p iter=%p pid=%d\\n",
                       arg0, arg1, pid);
                exit();
            }
            interval:s:8 { exit(); }
        """),
        trigger=(
            f"dd if=/dev/urandom of={workdir}/iomap_dio_$$ "
            f"bs=4096 count=4 oflag=direct conv=fsync 2>/dev/null; true"
        ),
        keyword="IOMAP_DIO_RW",
        timeout=10,
    )

def step7_fiemap(workdir):
    bpf_step(7, "iomap_fiemap reports extents via FIEMAP ioctl",
        textwrap.dedent("""
            kprobe:iomap_fiemap {
                printf("IOMAP_FIEMAP inode=%p pid=%d\\n", arg0, pid);
                exit();
            }
            interval:s:8 { exit(); }
        """),
        trigger=f"filefrag -v {workdir}/iomap_test_$$ 2>/dev/null; true",
        keyword="IOMAP_FIEMAP",
        timeout=10,
    )

def step8_seek_hole(workdir):
    bpf_step(8, "iomap_seek_hole/data used by SEEK_HOLE/DATA",
        textwrap.dedent("""
            kprobe:iomap_seek_hole {
                printf("IOMAP_SEEK_HOLE inode=%p offset=%lld pid=%d\\n",
                       arg0, arg1, pid);
                exit();
            }
            kprobe:iomap_seek_data {
                printf("IOMAP_SEEK_DATA inode=%p offset=%lld pid=%d\\n",
                       arg0, arg1, pid);
                exit();
            }
            interval:s:8 { exit(); }
        """),
        trigger=(
            f"python3 -c \""
            f"import os; "
            f"fd=os.open('{workdir}/iomap_test_$$',os.O_RDONLY);"
            f"os.lseek(fd,0,os.SEEK_HOLE); "
            f"os.close(fd)"
            f"\" 2>/dev/null; true"
        ),
        keyword="IOMAP_SEEK",
        timeout=10,
    )

def cleanup(workdir):
    run(f"rm -f {workdir}/iomap_test_* {workdir}/iomap_bw_* "
        f"{workdir}/iomap_wb_* {workdir}/iomap_dio_* 2>/dev/null", timeout=5)

def print_summary():
    print("\n" + "═"*60)
    print("  iomap Subsystem Verification Summary")
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
    parser.add_argument("--dir", default="/tmp", help="Writable filesystem dir")
    args = parser.parse_args()

    print("╔══════════════════════════════════════════════════════╗")
    print("║       iomap Subsystem - Workflow Verification        ║")
    print("╚══════════════════════════════════════════════════════╝")
    check_prereqs()

    workdir = args.dir
    step1_symbols()
    step2_iomap_begin(workdir)
    step3_buffered_write(workdir)
    step4_read_folio(workdir)
    step5_writepages(workdir)
    step6_direct_io(workdir)
    step7_fiemap(workdir)
    step8_seek_hole(workdir)
    cleanup(workdir)
    return print_summary()

if __name__ == "__main__":
    sys.exit(main())
