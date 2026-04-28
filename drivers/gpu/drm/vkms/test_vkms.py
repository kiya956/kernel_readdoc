#!/usr/bin/env python3
"""
VKMS (Virtual KMS) — bpftrace verification test
=================================================

Source: drivers/gpu/drm/vkms/
Kernel: noble-linux-oem
Scanned from: ~/canonical/kernel/noble-linux-oem

All probe targets verified against actual kernel source.
VKMS has NO exported symbols — module-internal only.
Test can be run on any system by loading vkms.ko.
"""

import subprocess
import sys
import os
import time
import signal

PROBE_TARGETS = [
    {
        "step": 1,
        "name": "Output init",
        "description": "vkms_output_init() wires CRTC+encoder+connector (vkms_output.c:8)",
        "primary_probe": "vkms_output_init",
        "alt_probes": ["vkms_config_default_create"],
        "source_file": "drivers/gpu/drm/vkms/vkms_output.c",
        "source_line": 8,
        "exported": False,
    },
    {
        "step": 2,
        "name": "Composer worker",
        "description": "vkms_composer_worker() blends planes and computes CRC (vkms_composer.c:491)",
        "primary_probe": "vkms_composer_worker",
        "alt_probes": ["vkms_set_composer"],
        "source_file": "drivers/gpu/drm/vkms/vkms_composer.c",
        "source_line": 491,
        "exported": False,
    },
    {
        "step": 3,
        "name": "Set CRC source",
        "description": "vkms_set_crc_source() enables CRC computation (vkms_composer.c:615)",
        "primary_probe": "vkms_set_crc_source",
        "alt_probes": ["vkms_verify_crc_source", "vkms_set_composer"],
        "source_file": "drivers/gpu/drm/vkms/vkms_composer.c",
        "source_line": 615,
        "exported": False,
    },
    {
        "step": 4,
        "name": "Verify CRC source",
        "description": "vkms_verify_crc_source() validates CRC source name (vkms_composer.c:584)",
        "primary_probe": "vkms_verify_crc_source",
        "alt_probes": ["vkms_set_crc_source"],
        "source_file": "drivers/gpu/drm/vkms/vkms_composer.c",
        "source_line": 584,
        "exported": False,
    },
    {
        "step": 5,
        "name": "Writeback row",
        "description": "vkms_writeback_row() writes blended pixels to WB buffer (vkms_formats.c:687)",
        "primary_probe": "vkms_writeback_row",
        "alt_probes": [],
        "source_file": "drivers/gpu/drm/vkms/vkms_formats.c",
        "source_line": 687,
        "exported": False,
    },
    {
        "step": 6,
        "name": "Config destroy",
        "description": "vkms_config_destroy() cleans up display config (vkms_config.c:109)",
        "primary_probe": "vkms_config_destroy",
        "alt_probes": ["vkms_config_destroy_plane"],
        "source_file": "drivers/gpu/drm/vkms/vkms_config.c",
        "source_line": 109,
        "exported": False,
    },
]


def check_root():
    if os.geteuid() != 0:
        print("ERROR: This test requires root (bpftrace needs CAP_SYS_ADMIN)")
        sys.exit(1)


def probe_exists(func_name):
    try:
        return subprocess.run(["grep", "-qw", func_name, "/proc/kallsyms"], timeout=5).returncode == 0
    except Exception:
        return False


def resolve_probe(target):
    if probe_exists(target["primary_probe"]):
        return target["primary_probe"]
    for alt in target.get("alt_probes", []):
        if probe_exists(alt):
            return alt
    return None


def build_bpftrace_script(active_probes):
    lines = ["BEGIN {", '  printf("VKMS bpftrace test started\\n");']
    for t, _ in active_probes:
        lines.append(f'  @step{t["step"]}_seen = 0;')
    lines.append("}\n")
    for t, p in active_probes:
        s = t["step"]
        lines += [f'kprobe:{p} {{', f'  @step{s}_seen = 1;', f'  @step{s}_count += 1;',
                  f'  printf("STEP {s} HIT: {p} (pid=%d comm=%s)\\n", pid, comm);', "}\n"]
    lines.append("END {")
    lines.append('  printf("\\n=== VKMS BPFTRACE TEST SUMMARY ===\\n");')
    for t, p in active_probes:
        s = t["step"]
        lines.append(f'  printf("Step {s} [{t["name"]}]: %s (hits=%d, probe={p})\\n",'
                     f' @step{s}_seen ? "PASS" : "FAIL", @step{s}_count);')
    lines.append('  printf("==================================\\n");')
    for t, _ in active_probes:
        lines += [f"  clear(@step{t['step']}_seen);", f"  clear(@step{t['step']}_count);"]
    lines.append("}")
    return "\n".join(lines)


def trigger_vkms_activity():
    """Load vkms module to trigger init paths, then unload."""
    print("[trigger] Loading vkms module...")
    try:
        subprocess.run(["modprobe", "vkms"], timeout=10)
        time.sleep(2)

        # Try to exercise the display — open the DRM device
        import glob as glib
        for node in sorted(glib.glob("/dev/dri/card*")):
            try:
                fd = os.open(node, os.O_RDWR)
                os.close(fd)
                print(f"[trigger] Opened {node}")
            except OSError:
                continue

        time.sleep(2)
        subprocess.run(["rmmod", "vkms"], timeout=10)
        print("[trigger] vkms loaded and unloaded")
    except Exception as e:
        print(f"[trigger] Warning: {e}")


def run_test(duration=15):
    print("=" * 60)
    print("VKMS (Virtual KMS) — bpftrace test")
    print("=" * 60)

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
        print("\nWARNING: No vkms probes found. Will load module during test.")

    print(f"\n  Active probes: {len(active)}/{len(PROBE_TARGETS)}")
    script = build_bpftrace_script(active) if active else 'BEGIN { printf("No probes\\n"); exit(); }'
    path = "/tmp/test_vkms_bpf.bt"
    with open(path, "w") as f:
        f.write(script)

    print(f"\n[bpftrace] Starting probes (duration: {duration}s)...")
    proc = subprocess.Popen(["bpftrace", path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    time.sleep(3)
    trigger_vkms_activity()
    time.sleep(max(1, duration - 5))
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
