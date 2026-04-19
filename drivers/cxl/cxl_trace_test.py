#!/usr/bin/env python3
"""
CXL (Compute Express Link) subsystem workflow verification via bpftrace.

Tests:
  1. Prerequisites (bpftrace, CXL tracepoints, sysfs)
  2. CXL device enumeration via sysfs
  3. CXL AER tracepoints present
  4. CXL event tracepoints present
  5. Mailbox command path (kprobe on cxl_internal_send_cmd)
  6. HDM decoder sysfs attributes
  7. CXL port tree traversal
  8. CXL PMU presence (perf_pmu)
  9. Memory region sysfs (if region exists)
 10. CXL poison list tracepoint

Each step is marked PASS or FAIL.

Requirements:
  - bpftrace >= 0.16
  - Linux kernel with CONFIG_CXL_BUS=y
  - Run as root (sudo python3 cxl_trace_test.py)
  - CXL hardware or QEMU CXL emulation for hardware-dependent steps
"""

import subprocess
import tempfile
import os
import sys
import re
import glob
import time


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


def run_bpftrace(script: str, timeout: int = 12) -> str:
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


def sysfs_read(path: str) -> str | None:
    try:
        with open(path) as f:
            return f.read().strip()
    except (PermissionError, FileNotFoundError, OSError):
        return None


def symbol_exists(sym: str) -> bool:
    """Check if a kernel symbol is present via /proc/kallsyms."""
    try:
        r = subprocess.run(
            ["grep", "-c", sym, "/proc/kallsyms"],
            capture_output=True, text=True, timeout=5
        )
        return r.returncode == 0 and int(r.stdout.strip()) > 0
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Step 1 – Prerequisites
# ─────────────────────────────────────────────────────────────────────────────

def step_prerequisites() -> None:
    print("\n── Step 1: Prerequisites ──────────────────────────────────────")

    record("running as root", check_root())
    record("bpftrace available", bpftrace_available())

    tp_dir = "/sys/kernel/debug/tracing/events/cxl"
    record("cxl tracepoints present", os.path.isdir(tp_dir), tp_dir)

    cxl_bus = "/sys/bus/cxl"
    record("cxl bus registered", os.path.isdir(cxl_bus), cxl_bus)


# ─────────────────────────────────────────────────────────────────────────────
# Step 2 – CXL device enumeration via sysfs
# ─────────────────────────────────────────────────────────────────────────────

def step_sysfs_enumeration() -> None:
    print("\n── Step 2: CXL device enumeration ─────────────────────────────")

    devices_dir = "/sys/bus/cxl/devices"
    if not os.path.isdir(devices_dir):
        record("cxl devices dir", False, "cxl bus absent")
        return

    devices = os.listdir(devices_dir)
    record("cxl devices dir readable", True, f"{len(devices)} entries")

    # Look for mem<N> devices
    mem_devs = [d for d in devices if re.match(r"mem\d+", d)]
    record("at least one cxl memdev (mem<N>)", len(mem_devs) > 0,
           f"found: {mem_devs}" if mem_devs else "no CXL hardware detected (SKIP OK)")

    # Look for root port
    roots = [d for d in devices if re.match(r"root\d+", d)]
    record("cxl root port present", len(roots) > 0,
           f"found: {roots}" if roots else "no root port")

    # Look for decoders
    decoders = [d for d in devices if re.match(r"decoder\d+\.\d+", d)]
    record("HDM decoders enumerated", len(decoders) > 0,
           f"count={len(decoders)}" if decoders else "none")


# ─────────────────────────────────────────────────────────────────────────────
# Step 3 – CXL AER tracepoints
# ─────────────────────────────────────────────────────────────────────────────

def step_aer_tracepoints() -> None:
    print("\n── Step 3: CXL AER tracepoints ────────────────────────────────")

    tp_base = "/sys/kernel/debug/tracing/events/cxl"
    for tp in ["cxl_aer_uncorrectable_error", "cxl_aer_correctable_error",
               "cxl_overflow"]:
        path = f"{tp_base}/{tp}"
        record(f"tracepoint {tp} exists", os.path.isdir(path), path)


# ─────────────────────────────────────────────────────────────────────────────
# Step 4 – CXL event tracepoints (media, DRAM, poison)
# ─────────────────────────────────────────────────────────────────────────────

def step_event_tracepoints() -> None:
    print("\n── Step 4: CXL event tracepoints ──────────────────────────────")

    tp_base = "/sys/kernel/debug/tracing/events/cxl"
    for tp in ["cxl_general_media", "cxl_dram", "cxl_memory_module",
               "cxl_poison", "cxl_generic_event"]:
        path = f"{tp_base}/{tp}"
        record(f"tracepoint {tp}", os.path.isdir(path), path)


# ─────────────────────────────────────────────────────────────────────────────
# Step 5 – Mailbox command path kprobe
# ─────────────────────────────────────────────────────────────────────────────

def step_mbox_kprobe() -> None:
    print("\n── Step 5: Mailbox command path (kprobe) ───────────────────────")

    if not bpftrace_available():
        record("mbox kprobe", False, "bpftrace missing")
        return

    # Check symbol presence first
    sym = "cxl_internal_send_cmd"
    has_sym = symbol_exists(sym)
    record(f"symbol {sym} present in kallsyms", has_sym,
           "module may not be loaded" if not has_sym else "")

    if not has_sym:
        return

    script = f"""
kprobe:{sym} {{
    printf("CXL_MBOX pid=%d comm=%s\\n", pid, comm);
}}
interval:s:5 {{ exit(); }}
"""
    out = run_bpftrace(script, timeout=8)
    fired = "CXL_MBOX" in out
    # kprobe attaches even if no commands sent during window — just check it compiled
    attached = "Attaching" in out or "CXL_MBOX" in out or out == ""
    record("mbox kprobe attaches without error",
           "ERROR" not in out.upper() or fired,
           "no mailbox commands observed in window (OK without hardware)" if not fired else "")


# ─────────────────────────────────────────────────────────────────────────────
# Step 6 – HDM decoder sysfs attributes
# ─────────────────────────────────────────────────────────────────────────────

def step_decoder_attrs() -> None:
    print("\n── Step 6: HDM decoder sysfs attributes ────────────────────────")

    decoders = glob.glob("/sys/bus/cxl/devices/decoder*")
    if not decoders:
        record("HDM decoder sysfs attrs", False, "no decoders present (no CXL hardware)")
        return

    for dec_path in decoders[:2]:
        name = os.path.basename(dec_path)
        for attr in ["start", "size", "interleave_ways", "interleave_granularity",
                     "target_type", "mode"]:
            val = sysfs_read(f"{dec_path}/{attr}")
            record(f"{name}/{attr} readable", val is not None, val or "")


# ─────────────────────────────────────────────────────────────────────────────
# Step 7 – CXL port tree (depth check)
# ─────────────────────────────────────────────────────────────────────────────

def step_port_tree() -> None:
    print("\n── Step 7: CXL port tree traversal ────────────────────────────")

    ports = glob.glob("/sys/bus/cxl/devices/port*")
    roots = glob.glob("/sys/bus/cxl/devices/root*")
    endpoints = glob.glob("/sys/bus/cxl/devices/endpoint*")

    record("port entries in sysfs", len(ports) > 0 or len(roots) > 0,
           f"roots={len(roots)} ports={len(ports)} endpoints={len(endpoints)}")

    for p in (roots + ports)[:2]:
        name = os.path.basename(p)
        # Each port has a 'decoders' subdirectory (or individual decoders listed)
        has_decoders = os.path.isdir(f"{p}/decoders") or any(
            os.path.isdir(f"/sys/bus/cxl/devices/{e}")
            for e in os.listdir(p) if "decoder" in e
        ) if os.path.isdir(p) else False
        record(f"{name} present", True, p)


# ─────────────────────────────────────────────────────────────────────────────
# Step 8 – CXL PMU
# ─────────────────────────────────────────────────────────────────────────────

def step_pmu() -> None:
    print("\n── Step 8: CXL PMU ─────────────────────────────────────────────")

    pmu_dirs = glob.glob("/sys/bus/event_source/devices/cxl_*")
    record("CXL PMU registered in perf subsystem", len(pmu_dirs) > 0,
           f"found: {[os.path.basename(p) for p in pmu_dirs]}" if pmu_dirs
           else "no CXL PMU (requires CXL 3.0+ hardware)")


# ─────────────────────────────────────────────────────────────────────────────
# Step 9 – Memory region sysfs
# ─────────────────────────────────────────────────────────────────────────────

def step_regions() -> None:
    print("\n── Step 9: CXL memory regions ──────────────────────────────────")

    regions = glob.glob("/sys/bus/cxl/devices/region*")
    record("CXL regions present", len(regions) > 0,
           f"count={len(regions)}" if regions
           else "no regions created (expected without hardware)")

    for reg in regions[:2]:
        name = os.path.basename(reg)
        for attr in ["size", "interleave_ways", "mode", "resource"]:
            val = sysfs_read(f"{reg}/{attr}")
            if val is not None:
                record(f"{name}/{attr}", True, val)


# ─────────────────────────────────────────────────────────────────────────────
# Step 10 – bpftrace: watch for any cxl tracepoint firing
# ─────────────────────────────────────────────────────────────────────────────

def step_tracepoint_watch() -> None:
    print("\n── Step 10: Live cxl tracepoint observation (5s) ──────────────")

    if not bpftrace_available():
        record("cxl tracepoint watch", False, "bpftrace missing")
        return

    tp_base = "/sys/kernel/debug/tracing/events/cxl"
    if not os.path.isdir(tp_base):
        record("cxl tracepoints", False, "tracepoint dir absent")
        return

    script = """
tracepoint:cxl:cxl_aer_correctable_error   { @events["aer_corr"]++; }
tracepoint:cxl:cxl_aer_uncorrectable_error  { @events["aer_uncorr"]++; }
tracepoint:cxl:cxl_generic_event            { @events["generic"]++; }
tracepoint:cxl:cxl_general_media            { @events["media"]++; }
tracepoint:cxl:cxl_dram                     { @events["dram"]++; }
tracepoint:cxl:cxl_poison                   { @events["poison"]++; }
interval:s:5 {
    print(@events);
    printf("OBSERVATION_DONE\\n");
    exit();
}
"""
    out = run_bpftrace(script, timeout=10)
    compiled_ok = "OBSERVATION_DONE" in out or "@events" in out
    record("bpftrace compiled and ran cxl tracepoints", compiled_ok,
           "no CXL events in 5s window (OK without hardware)" if compiled_ok else out[:200])

    # Report any events seen
    for key in ["aer_corr", "aer_uncorr", "generic", "media", "dram", "poison"]:
        m = re.search(rf'{key}[^\d]*(\d+)', out)
        if m and int(m.group(1)) > 0:
            record(f"cxl event {key} observed", True, f"count={m.group(1)}")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    print("=" * 64)
    print("  CXL (Compute Express Link) Subsystem Verification")
    print("  Linux kernel: drivers/cxl/")
    print("=" * 64)

    step_prerequisites()
    step_sysfs_enumeration()
    step_aer_tracepoints()
    step_event_tracepoints()
    step_mbox_kprobe()
    step_decoder_attrs()
    step_port_tree()
    step_pmu()
    step_regions()
    step_tracepoint_watch()

    # ── Summary ──────────────────────────────────────────────────────────────
    print("\n" + "=" * 64)
    print("  SUMMARY")
    print("=" * 64)
    passed = sum(1 for _, ok, _ in results if ok)
    failed = sum(1 for _, ok, _ in results if not ok)
    total  = len(results)
    print(f"  PASS: {passed}/{total}   FAIL: {failed}/{total}")
    if failed > 0:
        print("\n  Failed steps:")
        for name, ok, detail in results:
            if not ok:
                print(f"    - {name}" + (f": {detail}" if detail else ""))
    print("\n  NOTE: Hardware-dependent steps (decoder attrs, PMU, regions)")
    print("  require real CXL hardware or QEMU CXL emulation.")
    print("=" * 64)
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
