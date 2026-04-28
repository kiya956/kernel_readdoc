#!/usr/bin/env python3
"""
DRM GPU Scheduler (drm_sched) — bpftrace Workflow Verification
================================================================
Traces the drm_sched job submission → scheduling → HW dispatch → completion
pipeline step by step and marks each stage PASS or FAIL.

All probe targets verified against:
  ~/canonical/kernel/noble-linux-oem/drivers/gpu/drm/scheduler/

Requirements:
  - bpftrace >= 0.16  (sudo apt install bpftrace)
  - Root privileges    (sudo python3 test_drm_sched.py)
  - A DRM GPU that uses drm_sched (amdgpu, xe, nouveau, panfrost, etc.)
  - A running GPU workload or ability to trigger one (glxgears / vkcube)

Usage:
  sudo python3 test_drm_sched.py [--timeout 15] [--trigger glxgears]

Each step probes a kernel function, optionally runs a stimulus to exercise
the GPU scheduler path, and waits for the probe to fire — then marks PASS/FAIL.
"""

import argparse
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time

# ──────────────────────────────────────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────────────────────────────────────

BPFTRACE_BIN = "bpftrace"
PROBE_TIMEOUT = 10  # seconds per step

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def check_root():
    if os.geteuid() != 0:
        print("ERROR: must run as root (bpftrace needs CAP_SYS_ADMIN)")
        sys.exit(1)


def check_bpftrace():
    try:
        r = subprocess.run([BPFTRACE_BIN, "--version"],
                           capture_output=True, text=True, timeout=5)
        if r.returncode != 0:
            print(f"ERROR: {BPFTRACE_BIN} not working: {r.stderr.strip()}")
            sys.exit(1)
        print(f"[info] {r.stdout.strip()}")
    except FileNotFoundError:
        print(f"ERROR: {BPFTRACE_BIN} not found — install with: sudo apt install bpftrace")
        sys.exit(1)


def check_drm_sched_loaded():
    """Check if any driver using drm_sched is loaded."""
    try:
        r = subprocess.run(
            ["grep", "-r", "gpu_sched", "/proc/modules"],
            capture_output=True, text=True, timeout=5
        )
        if "gpu_sched" in r.stdout:
            print("[info] gpu_sched module loaded")
            return True
    except Exception:
        pass

    try:
        r = subprocess.run(
            ["grep", "-c", "drm_sched_init", "/proc/kallsyms"],
            capture_output=True, text=True, timeout=5
        )
        count = int(r.stdout.strip())
        if count > 0:
            print("[info] drm_sched symbols found in kallsyms")
            return True
    except Exception:
        pass

    print("[warn] drm_sched does not appear to be loaded — probes may not fire")
    return False


def function_exists_in_kallsyms(func_name):
    """Check if a kernel function is available for probing."""
    try:
        r = subprocess.run(
            ["grep", "-cw", func_name, "/proc/kallsyms"],
            capture_output=True, text=True, timeout=5
        )
        return int(r.stdout.strip()) > 0
    except Exception:
        return False


class BpftraceProbe:
    """Run a bpftrace one-liner and detect whether the probe fired."""

    def __init__(self, probe_spec, marker, timeout=PROBE_TIMEOUT):
        self.probe_spec = probe_spec
        self.marker = marker
        self.timeout = timeout
        self.fired = False
        self._proc = None
        self._output = ""

    def run(self):
        script = f'{self.probe_spec} {{ printf("{self.marker}\\n"); exit(); }}'
        self._proc = subprocess.Popen(
            [BPFTRACE_BIN, "-e", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        def _reader():
            try:
                out, err = self._proc.communicate(timeout=self.timeout)
                self._output = out + err
                if self.marker in self._output:
                    self.fired = True
            except subprocess.TimeoutExpired:
                self._proc.kill()
                try:
                    out, err = self._proc.communicate(timeout=3)
                    self._output = out + err
                    if self.marker in self._output:
                        self.fired = True
                except Exception:
                    pass

        t = threading.Thread(target=_reader, daemon=True)
        t.start()
        return t

    def stop(self):
        if self._proc and self._proc.poll() is None:
            self._proc.kill()
            try:
                self._proc.wait(timeout=3)
            except Exception:
                pass


class TriggerWorkload:
    """Launch a GPU workload to trigger the scheduler path."""

    def __init__(self, cmd="glxgears"):
        self.cmd = cmd
        self._proc = None

    def start(self):
        devnull = subprocess.DEVNULL
        try:
            self._proc = subprocess.Popen(
                self.cmd.split(),
                stdout=devnull, stderr=devnull,
                preexec_fn=os.setpgrp
            )
            time.sleep(1)
            print(f"[info] trigger workload started: {self.cmd} (pid={self._proc.pid})")
        except FileNotFoundError:
            print(f"[warn] trigger command '{self.cmd}' not found — "
                  f"relying on existing GPU activity")
            self._proc = None

    def stop(self):
        if self._proc and self._proc.poll() is None:
            try:
                os.killpg(os.getpgid(self._proc.pid), signal.SIGTERM)
                self._proc.wait(timeout=5)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            print("[info] trigger workload stopped")


# ──────────────────────────────────────────────────────────────────────────────
# Test Steps — all probe targets verified in noble-linux-oem source
#
# EXPORTED functions (kprobe-safe):
#   drm_sched_init, drm_sched_entity_init, drm_sched_job_init,
#   drm_sched_job_arm, drm_sched_entity_push_job, drm_sched_job_cleanup,
#   drm_sched_stop, drm_sched_start, drm_sched_fault
#
# NON-STATIC (internal, probeable if symbol present):
#   drm_sched_fence_scheduled, drm_sched_fence_finished,
#   drm_sched_fence_alloc, drm_sched_wakeup, drm_sched_entity_pop_job
#
# STATIC (may be probeable depending on CONFIG_KALLSYMS_ALL):
#   drm_sched_run_job_work, drm_sched_free_job_work, drm_sched_job_done,
#   drm_sched_job_timedout, drm_sched_select_entity, drm_sched_job_begin
# ──────────────────────────────────────────────────────────────────────────────

STEPS = [
    {
        "id": "sched_init",
        "name": "drm_sched_init — scheduler instance creation",
        "desc": "Verify drm_sched_init is callable (sched_main.c:1320, EXPORTED).",
        "probe": "kprobe:drm_sched_init",
        "needs_trigger": False,
        "note": "Init-time only; fires at driver load.",
        "fallback_check": "drm_sched_init",
    },
    {
        "id": "entity_init",
        "name": "drm_sched_entity_init — entity creation",
        "desc": "Verify scheduler entity is created (sched_entity.c:116, EXPORTED).",
        "probe": "kprobe:drm_sched_entity_init",
        "needs_trigger": True,
    },
    {
        "id": "job_init",
        "name": "drm_sched_job_init — job initialization",
        "desc": "Verify job is initialized with entity (sched_main.c:857, EXPORTED).",
        "probe": "kprobe:drm_sched_job_init",
        "needs_trigger": True,
    },
    {
        "id": "job_arm",
        "name": "drm_sched_job_arm — job armed with fence",
        "desc": "Verify job is armed: s_fence allocated, ID assigned (sched_main.c:890, EXPORTED).",
        "probe": "kprobe:drm_sched_job_arm",
        "needs_trigger": True,
    },
    {
        "id": "entity_push",
        "name": "drm_sched_entity_push_job — job queued to entity",
        "desc": "Verify job is pushed into entity's SPSC queue (sched_entity.c:576, EXPORTED).",
        "probe": "kprobe:drm_sched_entity_push_job",
        "needs_trigger": True,
    },
    {
        "id": "run_job",
        "name": "drm_sched_run_job_work — job dispatched to HW",
        "desc": "Verify scheduler dispatches job via run_job work (sched_main.c:1239, static).",
        "probe": "kprobe:drm_sched_run_job_work",
        "needs_trigger": True,
        "note": "Static function; may not be probeable without CONFIG_KALLSYMS_ALL.",
        "alt_probes": ["kprobe:drm_sched_wakeup"],
    },
    {
        "id": "fence_scheduled",
        "name": "drm_sched_fence_scheduled — scheduled fence signaled",
        "desc": "Verify 'scheduled' sub-fence is signaled after dispatch (sched_fence.c, non-static).",
        "probe": "kprobe:drm_sched_fence_scheduled",
        "needs_trigger": True,
    },
    {
        "id": "fence_finished",
        "name": "drm_sched_fence_finished — finished fence signaled",
        "desc": "Verify 'finished' sub-fence signals on HW completion (sched_fence.c, non-static).",
        "probe": "kprobe:drm_sched_fence_finished",
        "needs_trigger": True,
    },
    {
        "id": "free_job",
        "name": "drm_sched_free_job_work — job cleanup",
        "desc": "Verify free_job work runs after completion (sched_main.c:1220, static).",
        "probe": "kprobe:drm_sched_free_job_work",
        "needs_trigger": True,
        "note": "Static function; may not be probeable without CONFIG_KALLSYMS_ALL.",
        "alt_probes": ["kprobe:drm_sched_job_cleanup"],
    },
]

# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

def run_tests(args):
    check_root()
    check_bpftrace()
    sched_loaded = check_drm_sched_loaded()

    trigger = TriggerWorkload(args.trigger)
    results = []

    print()
    print("=" * 72)
    print("  DRM GPU Scheduler (drm_sched) — bpftrace Verification")
    print("  Source: noble-linux-oem drivers/gpu/drm/scheduler/")
    print("=" * 72)
    print()

    for step in STEPS:
        step_id = step["id"]
        step_name = step["name"]

        print(f"[step] {step_name}")
        print(f"       {step['desc']}")

        # Check if the probed function exists in kallsyms
        probe_func = step["probe"].split(":")[-1]
        if not function_exists_in_kallsyms(probe_func):
            found_alt = False
            for alt in step.get("alt_probes", []):
                alt_func = alt.split(":")[-1]
                if function_exists_in_kallsyms(alt_func):
                    step["probe"] = alt
                    probe_func = alt_func
                    print(f"       (using alternate probe: {alt})")
                    found_alt = True
                    break

            if not found_alt:
                fallback = step.get("fallback_check")
                if fallback and function_exists_in_kallsyms(fallback):
                    print(f"  →  SKIP  (function exists but is init-time only)")
                    results.append((step_id, step_name, "SKIP",
                                    "init-time function; symbol present"))
                    print()
                    continue
                elif not sched_loaded:
                    print(f"  →  SKIP  (drm_sched not loaded)")
                    results.append((step_id, step_name, "SKIP",
                                    "drm_sched module not loaded"))
                    print()
                    continue
                else:
                    print(f"  →  SKIP  (symbol {probe_func} not in kallsyms)")
                    results.append((step_id, step_name, "SKIP",
                                    f"symbol {probe_func} not found"))
                    print()
                    continue

        # Start trigger if needed
        if step.get("needs_trigger") and trigger._proc is None:
            trigger.start()

        # Run the bpftrace probe
        marker = f"SCHED_PROBE_{step_id}"
        probe = BpftraceProbe(step["probe"], marker, timeout=args.timeout)
        reader_thread = probe.run()

        time.sleep(1)

        if step.get("needs_trigger") and trigger._proc is not None:
            if trigger._proc.poll() is not None:
                trigger.start()

        reader_thread.join(timeout=args.timeout + 2)
        probe.stop()

        if probe.fired:
            print(f"  →  PASS")
            results.append((step_id, step_name, "PASS", ""))
        else:
            note = step.get("note", "probe did not fire within timeout")
            print(f"  →  FAIL  ({note})")
            results.append((step_id, step_name, "FAIL", note))

        print()

    # Cleanup
    trigger.stop()

    # ── Summary ──────────────────────────────────────────────────────────────
    print()
    print("=" * 72)
    print("  SUMMARY")
    print("=" * 72)
    print()
    print(f"{'Step':<20} {'Name':<50} {'Result':<6} Notes")
    print("-" * 100)

    pass_count = 0
    fail_count = 0
    skip_count = 0

    for step_id, name, result, notes in results:
        short_name = name[:48]
        print(f"{step_id:<20} {short_name:<50} {result:<6} {notes}")
        if result == "PASS":
            pass_count += 1
        elif result == "FAIL":
            fail_count += 1
        else:
            skip_count += 1

    print()
    print(f"Total: {pass_count} PASS, {fail_count} FAIL, {skip_count} SKIP "
          f"out of {len(results)} steps")
    print()

    return 0 if fail_count == 0 else 1


def main():
    parser = argparse.ArgumentParser(
        description="DRM GPU Scheduler bpftrace verification test"
    )
    parser.add_argument("--timeout", type=int, default=PROBE_TIMEOUT,
                        help=f"Seconds to wait per probe (default: {PROBE_TIMEOUT})")
    parser.add_argument("--trigger", type=str, default="glxgears",
                        help="GPU workload command to trigger scheduler activity "
                             "(default: glxgears)")
    args = parser.parse_args()

    sys.exit(run_tests(args))


if __name__ == "__main__":
    main()
