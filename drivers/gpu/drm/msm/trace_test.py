#!/usr/bin/env python3
"""
MSM DRM Driver — bpftrace Workflow Verification
================================================
Traces the MSM DRM command submission and display pipeline step by step
and marks each stage PASS or FAIL.

Requirements:
  - bpftrace >= 0.16  (sudo apt install bpftrace)
  - Root privileges  (sudo python3 trace_test.py)
  - An MSM DRM device at /dev/dri/card0 (set DRM_DEV env var)
  - python3-libdrm or ctypes (built-in fallback for ioctl numbers)

Usage:
  sudo python3 trace_test.py [--dev /dev/dri/card0] [--timeout 15]
"""

import argparse
import ctypes
import ctypes.util
import fcntl
import os
import struct
import subprocess
import sys
import tempfile
import threading
import time

DRM_DEV_DEFAULT = "/dev/dri/card0"
BPFTRACE_BIN = "bpftrace"
PROBE_TIMEOUT = 10

DRM_IOCTL_BASE = ord('d')

def _IOC(dir_, type_, nr, size):
    IOC_NRBITS = 8; IOC_TYPEBITS = 8
    IOC_SIZEBITS = 14; IOC_DIRBITS = 2
    IOC_NRSHIFT = 0
    IOC_TYPESHIFT = IOC_NRSHIFT + IOC_NRBITS
    IOC_SIZESHIFT = IOC_TYPESHIFT + IOC_TYPEBITS
    IOC_DIRSHIFT = IOC_SIZESHIFT + IOC_SIZEBITS
    return (dir_ << IOC_DIRSHIFT) | (type_ << IOC_TYPESHIFT) | \
           (nr << IOC_NRSHIFT) | (size << IOC_SIZESHIFT)

_IOWR = lambda t, nr, sz: _IOC(3, t, nr, sz)
_IOW  = lambda t, nr, sz: _IOC(1, t, nr, sz)
_IOR  = lambda t, nr, sz: _IOC(2, t, nr, sz)

DRM_IOCTL_VERSION     = _IOWR(DRM_IOCTL_BASE, 0x00, 24)
DRM_IOCTL_GET_UNIQUE  = _IOWR(DRM_IOCTL_BASE, 0x01, 16)
DRM_IOCTL_GET_MAGIC   = _IOR(DRM_IOCTL_BASE, 0x02, 8)
DRM_IOCTL_IRQ_BUSID   = _IOWR(DRM_IOCTL_BASE, 0x03, 20)
DRM_IOCTL_GET_MAP     = _IOWR(DRM_IOCTL_BASE, 0x04, 40)
DRM_IOCTL_GET_CLIENT  = _IOWR(DRM_IOCTL_BASE, 0x05, 24)
DRM_IOCTL_GET_STATS   = _IOR(DRM_IOCTL_BASE, 0x06, 56)
DRM_IOCTL_SET_VERSION = _IOWR(DRM_IOCTL_BASE, 0x07, 16)
DRM_IOCTL_MODESET_CTL = _IOW(DRM_IOCTL_BASE, 0x08, 8)
DRM_IOCTL_GEM_CLOSE   = _IOW(DRM_IOCTL_BASE, 0x09, 8)
DRM_IOCTL_GEM_FLINK   = _IOWR(DRM_IOCTL_BASE, 0x0a, 8)
DRM_IOCTL_GEM_OPEN    = _IOWR(DRM_IOCTL_BASE, 0x0b, 24)
DRM_IOCTL_GET_CAP     = _IOWR(DRM_IOCTL_BASE, 0x0c, 16)
DRM_IOCTL_SET_CLIENT_CAP = _IOW(DRM_IOCTL_BASE, 0x0d, 16)

DRM_IOCTL_MODE_GETRESOURCES = _IOWR(DRM_IOCTL_BASE, 0xA0, 32)
DRM_IOCTL_MODE_GETCRTC      = _IOWR(DRM_IOCTL_BASE, 0xA1, 64)
DRM_IOCTL_MODE_SETCRTC      = _IOWR(DRM_IOCTL_BASE, 0xA2, 88)
DRM_IOCTL_MODE_CURSOR       = _IOWR(DRM_IOCTL_BASE, 0xA3, 24)
DRM_IOCTL_MODE_GETGAMMA     = _IOWR(DRM_IOCTL_BASE, 0xA4, 16)
DRM_IOCTL_MODE_SETGAMMA     = _IOWR(DRM_IOCTL_BASE, 0xA5, 16)
DRM_IOCTL_MODE_GETENCODER   = _IOWR(DRM_IOCTL_BASE, 0xA6, 20)
DRM_IOCTL_MODE_GETCONNECTOR = _IOWR(DRM_IOCTL_BASE, 0xA7, 72)
DRM_IOCTL_MODE_ATOMIC       = _IOWR(DRM_IOCTL_BASE, 0xBC, 72)
DRM_IOCTL_MODE_CREATE_DUMB  = _IOWR(DRM_IOCTL_BASE, 0xB2, 24)
DRM_IOCTL_MODE_MAP_DUMB     = _IOWR(DRM_IOCTL_BASE, 0xB3, 16)
DRM_IOCTL_MODE_DESTROY_DUMB = _IOW(DRM_IOCTL_BASE, 0xB4, 8)
DRM_IOCTMODE_GETPLANERESOURCES = _IOWR(DRM_IOCTL_BASE, 0xB5, 16)

MSM_IOCTL_BASE = ord('m')
MSM_IOCTL_GET_PARAM  = _IOWR(MSM_IOCTL_BASE, 0x02, 32)
MSM_IOCTL_GEM_NEW    = _IOWR(MSM_IOCTL_BASE, 0x04, 20)
MSM_IOCTL_GEM_INFO   = _IOWR(MSM_IOCTL_BASE, 0x05, 64)
MSM_IOCTL_GEM_CPU_PREP = _IOW(MSM_IOCTL_BASE, 0x06, 24)
MSM_IOCTL_GEM_CPU_FINI = _IOW(MSM_IOCTL_BASE, 0x07, 8)
MSM_IOCTL_GEM_SUBMIT  = _IOWR(MSM_IOCTL_BASE, 0x08, 160)
MSM_IOCTL_WAIT_FENCE  = _IOWR(MSM_IOCTL_BASE, 0x09, 24)
MSM_IOCTL_GET_HDR    = _IOR(MSM_IOCTL_BASE, 0x0a, 8)

DRM_MSM_PARAM_GPU_ID  = 0x01

steps = []

class Step:
    def __init__(self, name, description, probe_cmd, trigger_fn):
        self.name = name
        self.description = description
        self.probe_cmd = probe_cmd
        self.trigger_fn = trigger_fn
        self.result = "WAITING"

    def run_probe(self, timeout):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.bt', delete=False) as f:
            f.write(self.probe_cmd)
            bpftrace_script = f.name

        hit = threading.Event()

        def monitor():
            proc = subprocess.Popen(
                [BPFTRACE_BIN, bpftrace_script],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            try:
                stdout, _ = proc.communicate(timeout=timeout)
                if b"HIT" in stdout:
                    hit.set()
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            finally:
                os.unlink(bpftrace_script)

        t = threading.Thread(target=monitor, daemon=True)
        t.start()

        time.sleep(0.5)

        try:
            self.trigger_fn()
        except Exception as e:
            pass

        t.join(timeout=timeout)

        if hit.is_set():
            self.result = "PASS"
        else:
            self.result = "FAIL"
        return self.result


def detect_drm_dev():
    for dev in [DRM_DEV_DEFAULT, "/dev/dri/card1", "/dev/dri/card2"]:
        if os.path.exists(dev):
            return dev
    return None


def main():
    parser = argparse.ArgumentParser(description="MSM DRM bpftrace verification")
    parser.add_argument("--dev", default=None, help="DRM device (e.g. /dev/dri/card0)")
    parser.add_argument("--timeout", type=int, default=PROBE_TIMEOUT, help="Per-step timeout (s)")
    args = parser.parse_args()

    dev = args.dev or detect_drm_dev()
    if not dev:
        print("ERROR: No DRM device found. Set --dev or DRM_DEV.")
        sys.exit(1)

    if os.geteuid() != 0:
        print("WARNING: Not running as root. bpftrace requires root.")

    try:
        subprocess.run([BPFTRACE_BIN, "--version"], capture_output=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        print("ERROR: bpftrace not found. Install with: sudo apt install bpftrace")
        sys.exit(1)

    drm_fd = os.open(dev, os.O_RDWR)
    print(f"Opened DRM device: {dev} (fd={drm_fd})")

    def trigger_drm_version():
        buf = struct.pack("i20s", 0, b'\x00' * 20)
        buf = struct.pack("i", 0) + b'\x00' * 20
        try:
            fcntl.ioctl(drm_fd, DRM_IOCTL_VERSION, struct.pack("i", 0) + b'\x00' * 20)
        except Exception:
            pass

    def trigger_mode_getresources():
        buf = struct.pack("IIIII", 0, 0, 0, 0, 0) + struct.pack("II", 0, 0)
        try:
            fcntl.ioctl(drm_fd, DRM_IOCTL_MODE_GETRESOURCES, buf)
        except Exception:
            pass

    def trigger_get_cap():
        buf = struct.pack("II", 0, 0)  # cap, value
        try:
            fcntl.ioctl(drm_fd, DRM_IOCTL_GET_CAP, buf)
        except Exception:
            pass

    def trigger_dumb_create():
        buf = struct.pack("IHHI", 64, 64, 32, 0)  # w, h, bpp, flags, handle, pitch, size
        try:
            fcntl.ioctl(drm_fd, DRM_IOCTL_MODE_CREATE_DUMB, buf)
        except Exception:
            pass

    steps = [
        Step(
            "drm_open_device",
            "DRM device open and version query",
            """BEGIN { } kprobe:drm_ioctl /arg1 == 0x00006400/ { printf("HIT\\n"); exit(); }""",
            trigger_drm_version,
        ),
        Step(
            "drm_get_cap",
            "DRM_IOCTL_GET_CAP — query driver capabilities",
            """BEGIN { } kprobe:drm_ioctl /arg1 == 0x00000c64/ { printf("HIT\\n"); exit(); }""",
            trigger_get_cap,
        ),
        Step(
            "drm_mode_getresources",
            "DRM_IOCTL_MODE_GETRESOURCES — enumerate KMS objects",
            """BEGIN { } kprobe:drm_ioctl /arg1 == 0x00a00064/ { printf("HIT\\n"); exit(); }""",
            trigger_mode_getresources,
        ),
        Step(
            "drm_gem_open",
            "DRM_IOCTL_GEM_OPEN — GEM object handle open",
            """BEGIN { } kprobe:drm_gem_open_ioctl { printf("HIT\\n"); exit(); }""",
            trigger_drm_version,
        ),
        Step(
            "drm_mode_create_dumb",
            "DRM_IOCTL_MODE_CREATE_DUMB — allocate dumb framebuffer",
            """BEGIN { } kprobe:drm_mode_create_dumb_ioctl { printf("HIT\\n"); exit(); }""",
            trigger_dumb_create,
        ),
        Step(
            "msm_ioctl_gem_submit",
            "MSM_IOCTL_GEM_SUBMIT — GPU command submission (if MSM driver)",
            """BEGIN { } kprobe:msm_ioctl_gem_submit { printf("HIT\\n"); exit(); }""",
            trigger_drm_version,
        ),
        Step(
            "msm_gpu_submit",
            "msm_gpu_submit — GPU submit function (if MSM driver loaded)",
            """BEGIN { } kprobe:msm_gpu_submit { printf("HIT\\n"); exit(); }""",
            trigger_drm_version,
        ),
    ]

    print(f"\n{'='*70}")
    print(f"  MSM DRM bpftrace Verification")
    print(f"  Device: {dev}")
    print(f"{'='*70}\n")

    results = []
    for step in steps:
        print(f"  [{step.name}] {step.description}...", end=" ", flush=True)
        result = step.run_probe(args.timeout)
        print(result)
        results.append((step.name, result))

    os.close(drm_fd)

    print(f"\n{'='*70}")
    print(f"  RESULTS SUMMARY")
    print(f"{'='*70}")
    print(f"  {'Step':<35} {'Result':<8}")
    print(f"  {'-'*35} {'-'*8}")
    passed = 0
    failed = 0
    for name, result in results:
        print(f"  {name:<35} {result:<8}")
        if result == "PASS":
            passed += 1
        else:
            failed += 1
    print(f"\n  Total: {len(results)} | PASS: {passed} | FAIL: {failed}")

    if failed > 0:
        sys.exit(1)

if __name__ == "__main__":
    main()
