#!/usr/bin/env python3
"""
Panfrost (ARM Mali) — bpftrace verification test
==================================================

Source: drivers/gpu/drm/panfrost/
Kernel: noble-linux-oem
Scanned from: ~/canonical/kernel/noble-linux-oem

All probe targets verified against actual kernel source.
Panfrost has NO exported symbols — all functions are module-internal.
Requires panfrost.ko loaded on ARM Mali Midgard/Bifrost hardware.
"""

import subprocess
import sys
import os
import time
import signal

PROBE_TARGETS = [
    {
        "step": 1,
        "name": "Device init",
        "description": "panfrost_device_init() initialises the GPU (panfrost_device.c:200)",
        "primary_probe": "panfrost_device_init",
        "alt_probes": ["panfrost_probe"],
        "source_file": "drivers/gpu/drm/panfrost/panfrost_device.c",
        "source_line": 200,
        "exported": False,
    },
    {
        "step": 2,
        "name": "GEM open",
        "description": "panfrost_gem_open() creates GPU mapping for BO (panfrost_gem.c:149)",
        "primary_probe": "panfrost_gem_open",
        "alt_probes": ["panfrost_gem_close"],
        "source_file": "drivers/gpu/drm/panfrost/panfrost_gem.c",
        "source_line": 149,
        "exported": False,
    },
    {
        "step": 3,
        "name": "MMU map",
        "description": "panfrost_mmu_map() maps BO into GPU address space (panfrost_mmu.c:426)",
        "primary_probe": "panfrost_mmu_map",
        "alt_probes": ["panfrost_mmu_unmap"],
        "source_file": "drivers/gpu/drm/panfrost/panfrost_mmu.c",
        "source_line": 426,
        "exported": False,
    },
    {
        "step": 4,
        "name": "Job push",
        "description": "panfrost_job_push() submits job to scheduler (panfrost_job.c:297)",
        "primary_probe": "panfrost_job_push",
        "alt_probes": ["panfrost_job_put"],
        "source_file": "drivers/gpu/drm/panfrost/panfrost_job.c",
        "source_line": 297,
        "exported": False,
    },
    {
        "step": 5,
        "name": "Job enable IRQs",
        "description": "panfrost_job_enable_interrupts() arms job completion IRQs (panfrost_job.c:408)",
        "primary_probe": "panfrost_job_enable_interrupts",
        "alt_probes": ["panfrost_job_suspend_irq"],
        "source_file": "drivers/gpu/drm/panfrost/panfrost_job.c",
        "source_line": 408,
        "exported": False,
    },
    {
        "step": 6,
        "name": "MMU reset",
        "description": "panfrost_mmu_reset() resets GPU MMU (panfrost_mmu.c:333)",
        "primary_probe": "panfrost_mmu_reset",
        "alt_probes": ["panfrost_mmu_init"],
        "source_file": "drivers/gpu/drm/panfrost/panfrost_mmu.c",
        "source_line": 333,
        "exported": False,
    },
    {
        "step": 7,
        "name": "Device reset",
        "description": "panfrost_device_reset() performs full GPU reset (panfrost_device.c:402)",
        "primary_probe": "panfrost_device_reset",
        "alt_probes": ["panfrost_device_fini"],
        "source_file": "drivers/gpu/drm/panfrost/panfrost_device.c",
        "source_line": 402,
        "exported": False,
    },
    {
        "step": 8,
        "name": "Job cleanup",
        "description": "panfrost_job_put() releases job resources (panfrost_job.c:365)",
        "primary_probe": "panfrost_job_put",
        "alt_probes": ["panfrost_job_fini"],
        "source_file": "drivers/gpu/drm/panfrost/panfrost_job.c",
        "source_line": 365,
        "exported": False,
    },
]


def check_root():
    if os.geteuid() != 0:
        print("ERROR: This test requires root (bpftrace needs CAP_SYS_ADMIN)")
        sys.exit(1)


def probe_exists(func_name: str) -> bool:
    try:
        ret = subprocess.run(["grep", "-qw", func_name, "/proc/kallsyms"], timeout=5)
        return ret.returncode == 0
    except Exception:
        return False


def resolve_probe(target: dict) -> str | None:
    if probe_exists(target["primary_probe"]):
        return target["primary_probe"]
    for alt in target.get("alt_probes", []):
        if probe_exists(alt):
            return alt
    return None


def build_bpftrace_script(active_probes):
    lines = ["BEGIN {", '  printf("Panfrost bpftrace test started\\n");']
    for t, _ in active_probes:
        lines.append(f'  @step{t["step"]}_seen = 0;')
    lines.append("}\n")
    for t, p in active_probes:
        s = t["step"]
        lines += [f'kprobe:{p} {{', f'  @step{s}_seen = 1;', f'  @step{s}_count += 1;',
                  f'  printf("STEP {s} HIT: {p} (pid=%d comm=%s)\\n", pid, comm);', "}\n"]
    lines.append("END {")
    lines.append('  printf("\\n=== PANFROST BPFTRACE TEST SUMMARY ===\\n");')
    for t, p in active_probes:
        s = t["step"]
        lines.append(f'  printf("Step {s} [{t["name"]}]: %s (hits=%d, probe={p})\\n",'
                     f' @step{s}_seen ? "PASS" : "FAIL", @step{s}_count);')
    lines.append('  printf("======================================\\n");')
    for t, _ in active_probes:
        lines += [f"  clear(@step{t['step']}_seen);", f"  clear(@step{t['step']}_count);"]
    lines.append("}")
    return "\n".join(lines)


def trigger_panfrost_activity():
    print("[trigger] Attempting to trigger panfrost activity...")
    import glob as glib
    for node in sorted(glib.glob("/dev/dri/renderD*") + glib.glob("/dev/dri/card*")):
        try:
            fd = os.open(node, os.O_RDWR)
            os.close(fd)
            print(f"[trigger] Opened and closed {node}")
        except OSError:
            continue


def run_test(duration=15):
    print("=" * 60)
    print("Panfrost (ARM Mali) — bpftrace test")
    print("=" * 60)

    lsmod = subprocess.run(["lsmod"], capture_output=True, text=True)
    if "panfrost" not in lsmod.stdout:
        print("\nWARNING: panfrost module not loaded!")
        print("This test requires ARM Mali Midgard/Bifrost hardware.\n")

    active, skipped = [], []
    for t in PROBE_TARGETS:
        p = resolve_probe(t)
        if p:
            active.append((t, p))
            m = "(primary)" if p == t["primary_probe"] else f"(alt: {p})"
            print(f"  Step {t['step']}: {t['name']} → {p} {m}")
        else:
            skipped.append(t)
            print(f"  Step {t['step']}: {t['name']} → SKIPPED")

    if not active:
        print("\nERROR: No probe targets found. Is panfrost.ko loaded?")
        sys.exit(1)

    print(f"\n  Active probes: {len(active)}/{len(PROBE_TARGETS)}")
    script = build_bpftrace_script(active)
    path = "/tmp/test_panfrost_bpf.bt"
    with open(path, "w") as f:
        f.write(script)

    print(f"\n[bpftrace] Starting probes (duration: {duration}s)...")
    proc = subprocess.Popen(["bpftrace", path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    time.sleep(3)
    trigger_panfrost_activity()
    time.sleep(max(1, duration - 3))
    proc.send_signal(signal.SIGINT)
    try:
        stdout, stderr = proc.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, stderr = proc.communicate()

    print("\n--- bpftrace output ---")
    print(stdout)

    results = {}
    for line in stdout.splitlines():
        if line.startswith("Step ") and ("PASS" in line or "FAIL" in line):
            results[int(line.split("[")[0].replace("Step ", "").strip())] = "PASS" in line

    print("\n" + "=" * 60)
    print("FINAL RESULTS")
    print("=" * 60)
    tp = 0
    for t in PROBE_TARGETS:
        s = t["step"]
        if s in results:
            st = "PASS ✓" if results[s] else "FAIL ✗"
            tp += results[s]
        elif t in [x for x, _ in active]:
            st = "FAIL ✗ (no hits)"
        else:
            st = "SKIP"
        print(f"  Step {s}: {t['name']:35s} {st}")
    print(f"\n  Score: {tp}/{len(PROBE_TARGETS)}")
    print("=" * 60)
    try:
        os.unlink(path)
    except OSError:
        pass
    return tp > 0


if __name__ == "__main__":
    check_root()
    d = int(sys.argv[1]) if len(sys.argv) > 1 else 15
    sys.exit(0 if run_test(d) else 1)
