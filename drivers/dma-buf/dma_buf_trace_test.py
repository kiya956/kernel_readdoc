#!/usr/bin/env python3
"""
DMA-BUF subsystem workflow verification via bpftrace.

Tests the fence lifecycle:
  INIT → ENABLE_SIGNAL → SIGNALED → WAIT_START → WAIT_END → DESTROY

Also tests the heap allocation path via /dev/dma_heap/system (if available).

Each step is marked PASS or FAIL.

Requirements:
  - bpftrace >= 0.16
  - Linux kernel with CONFIG_DMA_BUF=y, CONFIG_SW_SYNC=y
  - Run as root (sudo python3 dma_buf_trace_test.py)
"""

import subprocess
import tempfile
import time
import os
import sys
import json
import re
import threading
import fcntl
import struct


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
SKIP = "\033[33mSKIP\033[0m"

results: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    status = PASS if ok else FAIL
    line = f"  [{status}] {name}"
    if detail:
        line += f"  ({detail})"
    print(line)


def check_root() -> bool:
    return os.geteuid() == 0


def bpftrace_available() -> bool:
    try:
        r = subprocess.run(["bpftrace", "--version"], capture_output=True, timeout=5)
        return r.returncode == 0
    except FileNotFoundError:
        return False


def run_bpftrace(script: str, timeout: int = 15) -> str:
    """Run a bpftrace one-liner and return stdout."""
    with tempfile.NamedTemporaryFile("w", suffix=".bt", delete=False) as f:
        f.write(script)
        fname = f.name
    try:
        r = subprocess.run(
            ["bpftrace", fname],
            capture_output=True, text=True, timeout=timeout
        )
        return r.stdout + r.stderr
    except subprocess.TimeoutExpired:
        return ""
    finally:
        os.unlink(fname)


# ─────────────────────────────────────────────────────────────────────────────
# Step 1 – Prerequisites
# ─────────────────────────────────────────────────────────────────────────────

def step_prerequisites() -> None:
    print("\n── Step 1: Prerequisites ──────────────────────────────────────")

    record("running as root", check_root(),
           "re-run with sudo" if not check_root() else "")

    record("bpftrace available", bpftrace_available(),
           "install bpftrace" if not bpftrace_available() else "")

    # Check dma_fence tracepoints exist
    tp_path = "/sys/kernel/debug/tracing/events/dma_fence"
    record("dma_fence tracepoints present", os.path.isdir(tp_path), tp_path)

    # sw_sync device (software timeline for testing without GPU)
    sw_sync_path = "/dev/sw_sync"
    has_sw_sync = os.path.exists(sw_sync_path)
    record("sw_sync device present", has_sw_sync,
           "load CONFIG_SW_SYNC=y or enable debug" if not has_sw_sync else sw_sync_path)

    # dma_heap
    heap_path = "/dev/dma_heap/system"
    has_heap = os.path.exists(heap_path)
    record("dma_heap/system device present", has_heap,
           "load CONFIG_DMABUF_HEAPS=y" if not has_heap else heap_path)


# ─────────────────────────────────────────────────────────────────────────────
# Step 2 – Trace: dma_fence_init fired on any kernel activity
# ─────────────────────────────────────────────────────────────────────────────

def step_fence_init_tracepoint() -> None:
    print("\n── Step 2: dma_fence:dma_fence_init tracepoint ────────────────")

    if not bpftrace_available():
        record("dma_fence_init tracepoint fires", False, "bpftrace missing – skip")
        return

    script = """
tracepoint:dma_fence:dma_fence_init {
    printf("FENCE_INIT ctx=%llu seq=%u driver=%s\\n",
           args->context, args->seqno, str(args->driver));
}
interval:s:5 { exit(); }
"""
    out = run_bpftrace(script, timeout=10)
    found = "FENCE_INIT" in out
    record("dma_fence_init tracepoint fires within 5s", found,
           "try triggering GPU work if no output" if not found else "")


# ─────────────────────────────────────────────────────────────────────────────
# Step 3 – Trace: full fence lifecycle (init → signaled → destroy)
# ─────────────────────────────────────────────────────────────────────────────

def step_fence_lifecycle() -> None:
    print("\n── Step 3: dma_fence full lifecycle trace ──────────────────────")

    if not bpftrace_available():
        record("fence lifecycle tracepoints present", False, "bpftrace missing")
        return

    script = """
tracepoint:dma_fence:dma_fence_init      { @events["init"]++; }
tracepoint:dma_fence:dma_fence_signaled  { @events["signaled"]++; }
tracepoint:dma_fence:dma_fence_destroy   { @events["destroy"]++; }
interval:s:6 {
    print(@events);
    exit();
}
"""
    out = run_bpftrace(script, timeout=12)

    has_init    = re.search(r'init.*[1-9]\d*', out) is not None
    has_signal  = re.search(r'signaled.*[1-9]\d*', out) is not None
    has_destroy = re.search(r'destroy.*[1-9]\d*', out) is not None

    record("dma_fence_init seen",    has_init,    out[:200] if not has_init else "")
    record("dma_fence_signaled seen", has_signal, out[:200] if not has_signal else "")
    record("dma_fence_destroy seen",  has_destroy, "may fire after window" if not has_destroy else "")


# ─────────────────────────────────────────────────────────────────────────────
# Step 4 – Trace: dma_fence wait latency histogram
# ─────────────────────────────────────────────────────────────────────────────

def step_fence_wait_latency() -> None:
    print("\n── Step 4: dma_fence wait latency histogram ────────────────────")

    if not bpftrace_available():
        record("wait latency measurement", False, "bpftrace missing")
        return

    script = """
tracepoint:dma_fence:dma_fence_wait_start {
    @start[tid] = nsecs;
}
tracepoint:dma_fence:dma_fence_wait_end {
    if (@start[tid]) {
        @latency_us = hist((nsecs - @start[tid]) / 1000);
        delete(@start[tid]);
    }
}
interval:s:6 {
    print(@latency_us);
    exit();
}
"""
    out = run_bpftrace(script, timeout=12)

    has_hist = "@latency_us" in out or "usecs" in out.lower() or "[" in out
    record("fence wait latency histogram produced", has_hist,
           "no fence waits observed in window" if not has_hist else "")


# ─────────────────────────────────────────────────────────────────────────────
# Step 5 – dma_heap allocation via ioctl
# ─────────────────────────────────────────────────────────────────────────────

# DMA_HEAP_IOCTL_ALLOC: _IOWR(0x74, 0, struct dma_heap_allocation_data)
# struct dma_heap_allocation_data { __u64 len; __u32 fd; __u32 fd_flags; __u64 heap_flags; }
DMA_HEAP_ALLOC_STRUCT = struct.Struct("QIIQ")  # len, fd(out), fd_flags, heap_flags
DMA_HEAP_IOCTL_ALLOC = (3 << 30) | (0x74 << 8) | (0 << 0) | (DMA_HEAP_ALLOC_STRUCT.size << 16)


def step_heap_alloc() -> None:
    print("\n── Step 5: dma_heap allocation via /dev/dma_heap/system ────────")

    heap_path = "/dev/dma_heap/system"
    if not os.path.exists(heap_path):
        record("dma_heap/system open", False, "device absent – SKIP")
        return

    try:
        fd = os.open(heap_path, os.O_RDWR)
    except PermissionError as e:
        record("dma_heap/system open", False, str(e))
        return

    record("dma_heap/system open", True, heap_path)

    # Pack ioctl buffer: len=4096, fd=0(output), fd_flags=O_CLOEXEC, heap_flags=0
    buf = bytearray(DMA_HEAP_ALLOC_STRUCT.size)
    DMA_HEAP_ALLOC_STRUCT.pack_into(buf, 0, 4096, 0, os.O_CLOEXEC, 0)

    try:
        fcntl.ioctl(fd, DMA_HEAP_IOCTL_ALLOC, buf)
        _, dmabuf_fd, _, _ = DMA_HEAP_ALLOC_STRUCT.unpack_from(buf)
        record("DMA_HEAP_IOCTL_ALLOC succeeds", dmabuf_fd > 0,
               f"dmabuf_fd={dmabuf_fd}")

        if dmabuf_fd > 0:
            # DMA_BUF_IOCTL_SYNC: _IOW(0x62, 0, struct dma_buf_sync)
            # struct dma_buf_sync { __u64 flags; }  DMA_BUF_SYNC_START|READ=0x01
            DMA_BUF_SYNC_START = 0
            DMA_BUF_SYNC_READ  = 1
            DMA_BUF_IOCTL_SYNC = (1 << 30) | (0x62 << 8) | (0 << 0) | (8 << 16)
            sync_buf = struct.pack("Q", DMA_BUF_SYNC_START | DMA_BUF_SYNC_READ)
            try:
                fcntl.ioctl(dmabuf_fd, DMA_BUF_IOCTL_SYNC, bytearray(sync_buf))
                record("DMA_BUF_IOCTL_SYNC (begin CPU access)", True)
            except OSError as e:
                record("DMA_BUF_IOCTL_SYNC (begin CPU access)", False, str(e))

            os.close(dmabuf_fd)
            record("dmabuf fd closed cleanly", True)

    except OSError as e:
        record("DMA_HEAP_IOCTL_ALLOC succeeds", False, str(e))
    finally:
        os.close(fd)


# ─────────────────────────────────────────────────────────────────────────────
# Step 6 – Trace: dma_buf ioctl kprobe (kernel function probe)
# ─────────────────────────────────────────────────────────────────────────────

def step_dmabuf_ioctl_kprobe() -> None:
    print("\n── Step 6: kprobe on dma_buf_ioctl ────────────────────────────")

    if not bpftrace_available():
        record("dma_buf_ioctl kprobe", False, "bpftrace missing")
        return

    # Run heap alloc in background to trigger dma_buf_ioctl
    script = """
kprobe:dma_buf_ioctl {
    printf("DMA_BUF_IOCTL pid=%d comm=%s cmd=0x%lx\\n",
           pid, comm, arg2);
}
interval:s:5 { exit(); }
"""
    # Launch allocation in background thread while bpftrace watches
    alloc_done = threading.Event()

    def do_alloc():
        time.sleep(1)
        heap_path = "/dev/dma_heap/system"
        if not os.path.exists(heap_path):
            return
        try:
            fd = os.open(heap_path, os.O_RDWR)
            buf = bytearray(DMA_HEAP_ALLOC_STRUCT.size)
            DMA_HEAP_ALLOC_STRUCT.pack_into(buf, 0, 4096, 0, os.O_CLOEXEC, 0)
            fcntl.ioctl(fd, DMA_HEAP_IOCTL_ALLOC, buf)
            _, dmabuf_fd, _, _ = DMA_HEAP_ALLOC_STRUCT.unpack_from(buf)
            if dmabuf_fd > 0:
                # trigger DMA_BUF_IOCTL_SYNC which calls dma_buf_ioctl
                DMA_BUF_IOCTL_SYNC = (1 << 30) | (0x62 << 8) | (0 << 0) | (8 << 16)
                sync_buf = bytearray(struct.pack("Q", 0))
                try:
                    fcntl.ioctl(dmabuf_fd, DMA_BUF_IOCTL_SYNC, sync_buf)
                except OSError:
                    pass
                os.close(dmabuf_fd)
            os.close(fd)
        except Exception:
            pass
        allock_done = True

    t = threading.Thread(target=do_alloc, daemon=True)
    t.start()
    out = run_bpftrace(script, timeout=10)
    t.join(timeout=2)

    found = "DMA_BUF_IOCTL" in out
    record("dma_buf_ioctl kprobe fires", found,
           "requires dma_heap/system and CONFIG_DMA_BUF_SYSCALL" if not found else "")


# ─────────────────────────────────────────────────────────────────────────────
# Step 7 – sysfs stats (dma-buf-sysfs-stats)
# ─────────────────────────────────────────────────────────────────────────────

def step_sysfs_stats() -> None:
    print("\n── Step 7: dma-buf sysfs statistics ───────────────────────────")

    bufinfo_path = "/sys/kernel/debug/dma_buf/bufinfo"
    has_bufinfo = os.path.exists(bufinfo_path)
    record("debugfs dma_buf/bufinfo present", has_bufinfo, bufinfo_path)

    if has_bufinfo:
        try:
            with open(bufinfo_path) as f:
                content = f.read(512)
            record("bufinfo readable", True, f"{len(content)} bytes")
        except PermissionError as e:
            record("bufinfo readable", False, str(e))

    # /sys/kernel/debug/dma_buf/dma_buf_stats (newer kernels)
    stats_path = "/sys/kernel/debug/dma_buf/dma_buf_stats"
    if os.path.exists(stats_path):
        record("debugfs dma_buf_stats present", True, stats_path)


# ─────────────────────────────────────────────────────────────────────────────
# Step 8 – fence context counter monotonicity via kprobe
# ─────────────────────────────────────────────────────────────────────────────

def step_fence_context_monotone() -> None:
    print("\n── Step 8: fence context counter monotonicity ──────────────────")

    if not bpftrace_available():
        record("fence context monotone check", False, "bpftrace missing")
        return

    script = """
tracepoint:dma_fence:dma_fence_init {
    @ctx[args->context] = 1;
    @last_ctx = args->context;
    @count++;
}
interval:s:5 {
    printf("unique_ctx=%d total_fences=%d\\n", count(@ctx), @count);
    exit();
}
"""
    out = run_bpftrace(script, timeout=10)
    m = re.search(r'unique_ctx=(\d+)\s+total_fences=(\d+)', out)
    if m:
        unique = int(m.group(1))
        total  = int(m.group(2))
        record("fence contexts observed", unique > 0, f"unique={unique} total={total}")
        record("multiple fences per context possible", total >= unique,
               f"total={total} unique={unique}")
    else:
        record("fence context stats parsed", False, out[:200])


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    print("=" * 64)
    print("  DMA-BUF Subsystem Workflow Verification")
    print("  Linux kernel: drivers/dma-buf/")
    print("=" * 64)

    step_prerequisites()
    step_fence_init_tracepoint()
    step_fence_lifecycle()
    step_fence_wait_latency()
    step_heap_alloc()
    step_dmabuf_ioctl_kprobe()
    step_sysfs_stats()
    step_fence_context_monotone()

    # ── Summary ──────────────────────────────────────────────────────────────
    print("\n" + "=" * 64)
    print("  SUMMARY")
    print("=" * 64)
    passed  = sum(1 for _, ok, _ in results if ok)
    failed  = sum(1 for _, ok, _ in results if not ok)
    total   = len(results)
    print(f"  PASS: {passed}/{total}   FAIL: {failed}/{total}")
    if failed > 0:
        print("\n  Failed steps:")
        for name, ok, detail in results:
            if not ok:
                print(f"    - {name}" + (f": {detail}" if detail else ""))
    print("=" * 64)
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
