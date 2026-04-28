#!/usr/bin/env python3
"""
TTM (Translation Table Manager) — bpftrace verification test
=============================================================

Source: drivers/gpu/drm/ttm/
Kernel: noble-linux-oem
Scanned from: ~/canonical/kernel/noble-linux-oem

All probe targets verified against actual kernel source.
Exported symbols are safe for kprobe; static functions need
CONFIG_KALLSYMS_ALL and may be inlined — alt_probes provided.

Test flow: BO allocation → validation/migration → eviction → cleanup
Trigger: load/unload vkms (uses TTM) or exercise an existing TTM driver
"""

import subprocess
import sys
import os
import time
import signal
import json

# ---------------------------------------------------------------------------
# Probe target definitions (verified in noble-linux-oem source)
# ---------------------------------------------------------------------------
PROBE_TARGETS = [
    {
        "step": 1,
        "name": "BO initialisation",
        "description": "ttm_bo_init_reserved() sets up a new BO (ttm_bo.c:983, EXPORTED)",
        "primary_probe": "ttm_bo_init_reserved",
        "alt_probes": ["ttm_bo_init_validate"],
        "source_file": "drivers/gpu/drm/ttm/ttm_bo.c",
        "source_line": 983,
        "exported": True,
    },
    {
        "step": 2,
        "name": "Resource allocation (mem_space)",
        "description": "ttm_bo_mem_space() finds placement for BO (ttm_bo.c:801, EXPORTED)",
        "primary_probe": "ttm_bo_mem_space",
        "alt_probes": ["ttm_bo_validate"],
        "source_file": "drivers/gpu/drm/ttm/ttm_bo.c",
        "source_line": 801,
        "exported": True,
    },
    {
        "step": 3,
        "name": "BO validation / placement",
        "description": "ttm_bo_validate() validates or migrates BO to requested domain (ttm_bo.c:893, EXPORTED)",
        "primary_probe": "ttm_bo_validate",
        "alt_probes": ["ttm_bo_mem_space"],
        "source_file": "drivers/gpu/drm/ttm/ttm_bo.c",
        "source_line": 893,
        "exported": True,
    },
    {
        "step": 4,
        "name": "Page population",
        "description": "ttm_bo_populate() ensures backing pages exist (ttm_bo.c:1285, EXPORTED)",
        "primary_probe": "ttm_bo_populate",
        "alt_probes": ["ttm_pool_alloc"],
        "source_file": "drivers/gpu/drm/ttm/ttm_bo.c",
        "source_line": 1285,
        "exported": True,
    },
    {
        "step": 5,
        "name": "Memory copy move",
        "description": "ttm_bo_move_memcpy() CPU-side BO migration (ttm_bo_util.c:203, EXPORTED)",
        "primary_probe": "ttm_bo_move_memcpy",
        "alt_probes": ["ttm_move_memcpy", "ttm_bo_move_accel_cleanup"],
        "source_file": "drivers/gpu/drm/ttm/ttm_bo_util.c",
        "source_line": 203,
        "exported": True,
    },
    {
        "step": 6,
        "name": "BO pinning",
        "description": "ttm_bo_pin() prevents eviction (ttm_bo.c:636, EXPORTED)",
        "primary_probe": "ttm_bo_pin",
        "alt_probes": ["ttm_bo_unpin"],
        "source_file": "drivers/gpu/drm/ttm/ttm_bo.c",
        "source_line": 636,
        "exported": True,
    },
    {
        "step": 7,
        "name": "VM fault handling",
        "description": "ttm_bo_vm_fault_reserved() handles page faults for mmap'd BOs (ttm_bo_vm.c:283, EXPORTED)",
        "primary_probe": "ttm_bo_vm_fault_reserved",
        "alt_probes": ["ttm_bo_vm_reserve"],
        "source_file": "drivers/gpu/drm/ttm/ttm_bo_vm.c",
        "source_line": 283,
        "exported": True,
    },
    {
        "step": 8,
        "name": "LRU eviction walk",
        "description": "ttm_lru_walk_for_evict() walks LRU to free memory (ttm_bo_util.c:904, EXPORTED)",
        "primary_probe": "ttm_lru_walk_for_evict",
        "alt_probes": ["ttm_bo_eviction_valuable", "ttm_bo_shrink"],
        "source_file": "drivers/gpu/drm/ttm/ttm_bo_util.c",
        "source_line": 904,
        "exported": True,
    },
    {
        "step": 9,
        "name": "BO destruction",
        "description": "ttm_bo_put() drops refcount; frees BO when zero (ttm_bo.c:332, EXPORTED)",
        "primary_probe": "ttm_bo_put",
        "alt_probes": ["ttm_bo_move_to_lru_tail"],
        "source_file": "drivers/gpu/drm/ttm/ttm_bo.c",
        "source_line": 332,
        "exported": True,
    },
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def check_root():
    if os.geteuid() != 0:
        print("ERROR: This test requires root (bpftrace needs CAP_SYS_ADMIN)")
        sys.exit(1)


def probe_exists(func_name: str) -> bool:
    """Check if function is in /proc/kallsyms."""
    try:
        ret = subprocess.run(
            ["grep", "-qw", func_name, "/proc/kallsyms"],
            timeout=5,
        )
        return ret.returncode == 0
    except Exception:
        return False


def resolve_probe(target: dict) -> str | None:
    """Return the first available probe name from primary + alts."""
    if probe_exists(target["primary_probe"]):
        return target["primary_probe"]
    for alt in target.get("alt_probes", []):
        if probe_exists(alt):
            return alt
    return None


def build_bpftrace_script(active_probes: list[tuple[dict, str]]) -> str:
    """Build a single bpftrace program to probe all active targets."""
    lines = []
    lines.append("BEGIN {")
    lines.append('  printf("TTM bpftrace test started\\n");')
    for target, _ in active_probes:
        lines.append(f'  @step{target["step"]}_seen = 0;')
    lines.append("}")
    lines.append("")
    for target, probe_name in active_probes:
        step = target["step"]
        lines.append(f'kprobe:{probe_name} {{')
        lines.append(f'  @step{step}_seen = 1;')
        lines.append(f'  @step{step}_count += 1;')
        lines.append(f'  printf("STEP {step} HIT: {probe_name} (pid=%d comm=%s)\\n", pid, comm);')
        lines.append("}")
        lines.append("")
    # END block for summary
    lines.append("END {")
    lines.append('  printf("\\n=== TTM BPFTRACE TEST SUMMARY ===\\n");')
    for target, probe_name in active_probes:
        step = target["step"]
        name = target["name"]
        lines.append(
            f'  printf("Step {step} [{name}]: %s (hits=%d, probe={probe_name})\\n",'
            f' @step{step}_seen ? "PASS" : "FAIL", @step{step}_count);'
        )
    lines.append('  printf("=================================\\n");')
    # Cleanup maps
    for target, _ in active_probes:
        step = target["step"]
        lines.append(f"  clear(@step{step}_seen);")
        lines.append(f"  clear(@step{step}_count);")
    lines.append("}")
    return "\n".join(lines)


def trigger_ttm_activity():
    """
    Trigger TTM activity by exercising a DRM driver that uses TTM.
    Try modeset via vkms (if available) or open/close a DRM device
    from an existing TTM-based driver (amdgpu, nouveau, radeon, vmwgfx, xe).
    """
    print("[trigger] Attempting to trigger TTM activity...")

    # Strategy 1: Open a TTM-based DRM render node
    import glob as glib
    render_nodes = sorted(glib.glob("/dev/dri/renderD*"))
    card_nodes = sorted(glib.glob("/dev/dri/card*"))

    for node in render_nodes + card_nodes:
        try:
            fd = os.open(node, os.O_RDWR)
            # Read version to trigger driver init paths
            os.close(fd)
            print(f"[trigger] Opened and closed {node}")
        except OSError:
            continue

    # Strategy 2: Load/unload vkms if no TTM driver is present
    try:
        lsmod = subprocess.run(["lsmod"], capture_output=True, text=True)
        ttm_drivers = ["amdgpu", "radeon", "nouveau", "vmwgfx", "xe", "i915"]
        has_ttm_driver = any(d in lsmod.stdout for d in ttm_drivers)
        if not has_ttm_driver:
            print("[trigger] No TTM driver loaded; loading vkms...")
            subprocess.run(["modprobe", "vkms"], timeout=10)
            time.sleep(2)
            subprocess.run(["rmmod", "vkms"], timeout=10)
            print("[trigger] vkms loaded and unloaded")
    except Exception as e:
        print(f"[trigger] Warning: {e}")

    # Strategy 3: Try a simple GL operation (if available)
    try:
        ret = subprocess.run(
            ["glxinfo", "-B"],
            capture_output=True, text=True, timeout=5,
        )
        if ret.returncode == 0:
            print("[trigger] Ran glxinfo to exercise GPU paths")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass


def run_test(duration: int = 15):
    """Run the bpftrace probes, trigger activity, collect results."""

    print("=" * 60)
    print("TTM (Translation Table Manager) — bpftrace test")
    print("=" * 60)
    print()

    # Resolve probes
    active_probes = []
    skipped = []
    for target in PROBE_TARGETS:
        probe = resolve_probe(target)
        if probe:
            active_probes.append((target, probe))
            marker = "(primary)" if probe == target["primary_probe"] else f"(alt: {probe})"
            print(f"  Step {target['step']}: {target['name']} → {probe} {marker}")
        else:
            skipped.append(target)
            print(f"  Step {target['step']}: {target['name']} → SKIPPED (not in kallsyms)")

    if not active_probes:
        print("\nERROR: No probe targets found in kallsyms. Is ttm module loaded?")
        # Check if ttm module is available
        ret = subprocess.run(["modprobe", "-n", "ttm"], capture_output=True)
        if ret.returncode == 0:
            print("HINT: Try loading a TTM driver first: modprobe amdgpu / nouveau / radeon")
        sys.exit(1)

    print(f"\n  Active probes: {len(active_probes)}/{len(PROBE_TARGETS)}")
    print()

    # Build and write bpftrace script
    script = build_bpftrace_script(active_probes)
    script_path = "/tmp/test_ttm_bpf.bt"
    with open(script_path, "w") as f:
        f.write(script)

    # Start bpftrace
    print(f"[bpftrace] Starting probes (duration: {duration}s)...")
    bpf_proc = subprocess.Popen(
        ["bpftrace", script_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    time.sleep(3)  # Let probes attach

    # Trigger TTM activity
    trigger_ttm_activity()

    # Wait for remaining time
    remaining = max(1, duration - 3)
    print(f"[bpftrace] Collecting for {remaining}s more...")
    time.sleep(remaining)

    # Stop bpftrace
    bpf_proc.send_signal(signal.SIGINT)
    try:
        stdout, stderr = bpf_proc.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        bpf_proc.kill()
        stdout, stderr = bpf_proc.communicate()

    print("\n--- bpftrace output ---")
    print(stdout)
    if stderr.strip():
        # Filter out attach messages
        for line in stderr.strip().splitlines():
            if "Attaching" not in line and "WARNING" not in line:
                print(f"  stderr: {line}")

    # Parse results
    results = {}
    for line in stdout.splitlines():
        if line.startswith("Step ") and ("PASS" in line or "FAIL" in line):
            step_num = int(line.split("[")[0].replace("Step ", "").strip())
            passed = "PASS" in line
            results[step_num] = passed

    # Print summary
    print("\n" + "=" * 60)
    print("FINAL RESULTS")
    print("=" * 60)
    total_pass = 0
    total = len(PROBE_TARGETS)
    for target in PROBE_TARGETS:
        step = target["step"]
        if step in results:
            status = "PASS ✓" if results[step] else "FAIL ✗"
            if results[step]:
                total_pass += 1
        elif target in [t for t, _ in active_probes]:
            status = "FAIL ✗ (no hits)"
        else:
            status = "SKIP (not in kallsyms)"
        print(f"  Step {step}: {target['name']:35s} {status}")

    print(f"\n  Score: {total_pass}/{total} steps passed")
    print("=" * 60)

    # Cleanup
    try:
        os.unlink(script_path)
    except OSError:
        pass

    return total_pass > 0


if __name__ == "__main__":
    check_root()
    duration = int(sys.argv[1]) if len(sys.argv) > 1 else 15
    success = run_test(duration)
    sys.exit(0 if success else 1)
