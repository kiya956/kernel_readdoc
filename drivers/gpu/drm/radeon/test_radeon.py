#!/usr/bin/env python3
"""
Radeon DRM driver — bpftrace verification test
================================================

Source: drivers/gpu/drm/radeon/
Kernel: noble-linux-oem
Scanned from: ~/canonical/kernel/noble-linux-oem

All probe targets verified against actual kernel source.
Radeon has NO exported symbols — all functions are module-internal.
Probes require CONFIG_KALLSYMS_ALL and the radeon module to be loaded.

Test flow: device init → GEM create → CS submit → fence → cleanup
Trigger: open/close a radeon DRM device node or run glxinfo
"""

import subprocess
import sys
import os
import time
import signal

# ---------------------------------------------------------------------------
# Probe target definitions (verified in noble-linux-oem source)
# All radeon functions are module-internal (no EXPORT_SYMBOL).
# Probes work when radeon.ko is loaded and CONFIG_KALLSYMS_ALL=y.
# ---------------------------------------------------------------------------
PROBE_TARGETS = [
    {
        "step": 1,
        "name": "Device init",
        "description": "radeon_device_init() initialises the GPU (radeon_device.c:1278)",
        "primary_probe": "radeon_device_init",
        "alt_probes": ["radeon_pci_probe"],
        "source_file": "drivers/gpu/drm/radeon/radeon_device.c",
        "source_line": 1278,
        "exported": False,
    },
    {
        "step": 2,
        "name": "GEM object creation",
        "description": "radeon_gem_object_create() allocates a GEM BO (radeon_gem.c:93)",
        "primary_probe": "radeon_gem_object_create",
        "alt_probes": ["radeon_gem_create_ioctl", "radeon_gem_init"],
        "source_file": "drivers/gpu/drm/radeon/radeon_gem.c",
        "source_line": 93,
        "exported": False,
    },
    {
        "step": 3,
        "name": "CS parser init",
        "description": "radeon_cs_parser_init() begins command submission parsing (radeon_cs.c:265)",
        "primary_probe": "radeon_cs_parser_init",
        "alt_probes": ["radeon_cs_ioctl"],
        "source_file": "drivers/gpu/drm/radeon/radeon_cs.c",
        "source_line": 265,
        "exported": False,
    },
    {
        "step": 4,
        "name": "CS ioctl",
        "description": "radeon_cs_ioctl() handles DRM_IOCTL_RADEON_CS (radeon_cs.c:669)",
        "primary_probe": "radeon_cs_ioctl",
        "alt_probes": ["radeon_cs_parser_init"],
        "source_file": "drivers/gpu/drm/radeon/radeon_cs.c",
        "source_line": 669,
        "exported": False,
    },
    {
        "step": 5,
        "name": "Fence emit",
        "description": "radeon_fence_emit() writes fence sequence to ring (radeon_fence.c:133)",
        "primary_probe": "radeon_fence_emit",
        "alt_probes": ["radeon_fence_process", "radeon_fence_wait"],
        "source_file": "drivers/gpu/drm/radeon/radeon_fence.c",
        "source_line": 133,
        "exported": False,
    },
    {
        "step": 6,
        "name": "Fence process",
        "description": "radeon_fence_process() polls GPU for completed fences (radeon_fence.c:319)",
        "primary_probe": "radeon_fence_process",
        "alt_probes": ["radeon_fence_signaled"],
        "source_file": "drivers/gpu/drm/radeon/radeon_fence.c",
        "source_line": 319,
        "exported": False,
    },
    {
        "step": 7,
        "name": "GPU reset",
        "description": "radeon_gpu_reset() performs GPU recovery (radeon_device.c:1755)",
        "primary_probe": "radeon_gpu_reset",
        "alt_probes": ["radeon_asic_reset"],
        "source_file": "drivers/gpu/drm/radeon/radeon_device.c",
        "source_line": 1755,
        "exported": False,
    },
    {
        "step": 8,
        "name": "Suspend",
        "description": "radeon_suspend_kms() handles system suspend (radeon_device.c:1544)",
        "primary_probe": "radeon_suspend_kms",
        "alt_probes": ["radeon_resume_kms"],
        "source_file": "drivers/gpu/drm/radeon/radeon_device.c",
        "source_line": 1544,
        "exported": False,
    },
    {
        "step": 9,
        "name": "BO destruction (put)",
        "description": "radeon_gem_fini() cleans up GEM subsystem (radeon_gem.c:187)",
        "primary_probe": "radeon_gem_fini",
        "alt_probes": ["radeon_device_fini"],
        "source_file": "drivers/gpu/drm/radeon/radeon_gem.c",
        "source_line": 187,
        "exported": False,
    },
]


def check_root():
    if os.geteuid() != 0:
        print("ERROR: This test requires root (bpftrace needs CAP_SYS_ADMIN)")
        sys.exit(1)


def probe_exists(func_name: str) -> bool:
    try:
        ret = subprocess.run(
            ["grep", "-qw", func_name, "/proc/kallsyms"],
            timeout=5,
        )
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


def build_bpftrace_script(active_probes: list[tuple[dict, str]]) -> str:
    lines = []
    lines.append("BEGIN {")
    lines.append('  printf("Radeon bpftrace test started\\n");')
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
    lines.append("END {")
    lines.append('  printf("\\n=== RADEON BPFTRACE TEST SUMMARY ===\\n");')
    for target, probe_name in active_probes:
        step = target["step"]
        name = target["name"]
        lines.append(
            f'  printf("Step {step} [{name}]: %s (hits=%d, probe={probe_name})\\n",'
            f' @step{step}_seen ? "PASS" : "FAIL", @step{step}_count);'
        )
    lines.append('  printf("====================================\\n");')
    for target, _ in active_probes:
        step = target["step"]
        lines.append(f"  clear(@step{step}_seen);")
        lines.append(f"  clear(@step{step}_count);")
    lines.append("}")
    return "\n".join(lines)


def trigger_radeon_activity():
    """Trigger radeon activity via DRM device nodes or GL commands."""
    print("[trigger] Attempting to trigger radeon activity...")

    import glob as glib
    for node in sorted(glib.glob("/dev/dri/renderD*") + glib.glob("/dev/dri/card*")):
        try:
            fd = os.open(node, os.O_RDWR)
            os.close(fd)
            print(f"[trigger] Opened and closed {node}")
        except OSError:
            continue

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
    print("=" * 60)
    print("Radeon DRM driver — bpftrace test")
    print("=" * 60)

    # Check if radeon module is loaded
    lsmod = subprocess.run(["lsmod"], capture_output=True, text=True)
    if "radeon" not in lsmod.stdout:
        print("\nWARNING: radeon module not loaded!")
        print("This test requires a system with Radeon GPU and radeon.ko loaded.")
        print("Steps 1 (device_init) and 7-9 only fire at module load/unload time.")
        print("Steps 2-6 require active GL/Vulkan rendering.\n")

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
        print("\nERROR: No probe targets found. Is radeon.ko loaded?")
        sys.exit(1)

    print(f"\n  Active probes: {len(active_probes)}/{len(PROBE_TARGETS)}")

    script = build_bpftrace_script(active_probes)
    script_path = "/tmp/test_radeon_bpf.bt"
    with open(script_path, "w") as f:
        f.write(script)

    print(f"\n[bpftrace] Starting probes (duration: {duration}s)...")
    bpf_proc = subprocess.Popen(
        ["bpftrace", script_path],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )

    time.sleep(3)
    trigger_radeon_activity()

    remaining = max(1, duration - 3)
    print(f"[bpftrace] Collecting for {remaining}s more...")
    time.sleep(remaining)

    bpf_proc.send_signal(signal.SIGINT)
    try:
        stdout, stderr = bpf_proc.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        bpf_proc.kill()
        stdout, stderr = bpf_proc.communicate()

    print("\n--- bpftrace output ---")
    print(stdout)
    if stderr.strip():
        for line in stderr.strip().splitlines():
            if "Attaching" not in line and "WARNING" not in line:
                print(f"  stderr: {line}")

    results = {}
    for line in stdout.splitlines():
        if line.startswith("Step ") and ("PASS" in line or "FAIL" in line):
            step_num = int(line.split("[")[0].replace("Step ", "").strip())
            results[step_num] = "PASS" in line

    print("\n" + "=" * 60)
    print("FINAL RESULTS")
    print("=" * 60)
    total_pass = 0
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

    print(f"\n  Score: {total_pass}/{len(PROBE_TARGETS)} steps passed")
    print("=" * 60)

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
