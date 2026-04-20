# Linux Kernel DRM Accelerator (accel) Subsystem

## Overview

`drivers/accel` is the kernel framework for **non-GPU compute accelerators**:
NPUs (Neural Processing Units), AI inference chips, and ML accelerators.
Introduced in Linux 6.2, it reuses the DRM infrastructure (device lifecycle,
GEM memory management, file ops, debugfs) while exposing devices under a
dedicated `/dev/accel/accel<N>` namespace with its own major number
(`ACCEL_MAJOR = 261`).

Source: `drivers/accel/`

**Hardware drivers in-tree (this kernel):**

| Driver | Hardware | Vendor |
|--------|----------|--------|
| `ivpu` | Intel NPU (MTL/ARL/LNL/PTL/WCL) | Intel |
| `amdxdna` | AMD XDNA NPU (Ryzen AI, Phoenix/Hawk/Strix) | AMD |
| `habanalabs` | Gaudi / Gaudi2 / Goya AI accelerators | Intel/Habana |
| `qaic` | Cloud AI 100 (AIC080/AIC100) | Qualcomm |

---

## Subsystem Stack

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            Userspace                                         │
│                                                                              │
│  ┌───────────────────┐  ┌─────────────────┐  ┌──────────────────────────┐  │
│  │ Intel OpenVINO /  │  │ AMD ROCm / XRT  │  │ Qualcomm SNPE /          │  │
│  │  PyTorch NPU      │  │  (XDNA runtime) │  │  HL-SMI (Gaudi)          │  │
│  └────────┬──────────┘  └───────┬─────────┘  └───────────┬──────────────┘  │
│           │                     │                         │                  │
│           ▼                     ▼                         ▼                  │
│       /dev/accel/accel0    /dev/accel/accel1        /dev/accel/accel2        │
└───────────┼─────────────────────┼─────────────────────────┼──────────────────┘
            │ open/ioctl/mmap     │                         │
            ▼                     ▼                         ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                      accel core (drm_accel.c)                                │
│                                                                              │
│  accel_class (/sys/class/accel)  accel_minors_xa (xarray)                   │
│  accel_open() → drm_open_helper()     ACCEL_MAJOR = 261                      │
│  accel_set_device_instance_params()   debugfs: /sys/kernel/debug/accel/      │
└──────────────────────────┬──────────────────────────────────────────────────┘
                           │  reuses DRM core
┌──────────────────────────▼──────────────────────────────────────────────────┐
│                         DRM Core (drm_device / drm_driver)                   │
│                                                                              │
│  drm_dev_alloc()   drm_dev_register()   GEM (drm_gem_object)                │
│  drm_open_helper() drm_ioctl()          PRIME (dma-buf export/import)        │
│  drm_file          drm_master           drm_minor (accel minor)              │
└──────────────────────────┬──────────────────────────────────────────────────┘
                           │
        ┌──────────────────┼──────────────────┬───────────────────┐
        ▼                  ▼                  ▼                   ▼
┌───────────────┐  ┌───────────────┐  ┌──────────────┐  ┌───────────────┐
│  ivpu (Intel) │  │  amdxdna      │  │ habanalabs   │  │  qaic         │
│               │  │  (AMD)        │  │ (Intel/Habana│  │ (Qualcomm)    │
│  VPU 37xx     │  │               │  │              │  │               │
│  VPU 40xx     │  │  AIE2 engine  │  │  Goya        │  │  AIC080       │
│  VPU 50xx     │  │  NPU1/2/4/5/6 │  │  Gaudi       │  │  AIC100       │
│  VPU 60xx     │  │               │  │  Gaudi2      │  │               │
└───────┬───────┘  └───────┬───────┘  └──────┬───────┘  └───────┬───────┘
        │                  │                  │                   │
        ▼                  ▼                  ▼                   ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                    PCI / Platform Bus                                         │
│  pci_driver.probe() → firmware load → IOMMU mapping → IPC/mailbox setup     │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Layer-by-Layer Explanation

### 1. accel core (`drm_accel.c`)

A thin shim that:
- Registers `ACCEL_MAJOR` chardev with a stub `fops` that redirects to the
  actual driver fops on `open()`
- Creates `/sys/class/accel` and per-device `accel_minor` sysfs nodes
- Initialises an `xarray` (`accel_minors_xa`) for O(log n) minor lookup
- Plugs into `drm_debugfs` for per-device info

The framework is intentionally minimal — all heavy lifting (GEM, file tracking,
ioctl dispatch) delegates to existing DRM core infrastructure.

### 2. DRM reuse

Each accelerator driver:
1. Allocates a `drm_device` with `devm_drm_dev_alloc()`
2. Registers a `drm_driver` with `DRIVER_COMPUTE_ACCEL` flag (no render/display)
3. Gets a `drm_minor` of type `DRM_MINOR_ACCEL` (appears as `/dev/accel/accel<N>`)
4. Uses **GEM** (`drm_gem_object`) for device memory management
5. Uses **PRIME** (`dma-buf`) for cross-device buffer sharing

### 3. ivpu — Intel NPU / VPU

The most OEM-relevant driver: ships in every Intel Core Ultra laptop.

| Silicon | PCI ID | HW IP |
|---------|--------|-------|
| Meteor Lake (MTL) | `0x7d1d` | VPU 37xx |
| Arrow Lake (ARL) | `0xad1d` | VPU 40xx |
| Lunar Lake (LNL) | `0x643e` | VPU 50xx |
| Panther Lake P (PTL-P) | `0xb03e` | VPU 60xx |
| Wildcat Lake (WCL) | `0xfd3e` | VPU 60xx |

**Key components:**

| File | Role |
|------|------|
| `ivpu_drv.c` | PCI probe, module params, file_priv lifecycle |
| `ivpu_fw.c` | Firmware load (request_firmware), boot handshake |
| `ivpu_ipc.c` | Doorbell-based IPC channel to firmware (JSM protocol) |
| `ivpu_job.c` | Job submission: cmdq → firmware → completion |
| `ivpu_gem.c` | GEM allocator (shmem + IOMMU mapping) |
| `ivpu_mmu.c` | NPU-internal IOMMU (SID-based context isolation) |
| `ivpu_hw_*.c` | Per-IP hardware register programming |
| `ivpu_pm.c` | Runtime PM: D0i3 suspend, D0 resume |

**Scheduling modes:** OS scheduler (DRM scheduler) or hardware scheduler (HW cmdq).

### 4. amdxdna — AMD XDNA NPU (Ryzen AI)

The AMD Neural Processing Unit found in Ryzen AI 300/Phoenix/Hawk Point.

Key abstractions:
- `amdxdna_hwctx` — hardware context (maps to an AIE2 column partition)
- `amdxdna_gem_obj` — GEM object (device memory or HMM-backed host memory)
- `aie2_ctx.c` — AIE2 (AI Engine 2) column management, partition assignment
- `aie2_message.c` — Mailbox protocol to firmware
- `aie2_solver.c` — Resource solver: assigns AIE columns to hardware contexts
- `amdxdna_mailbox.c` — Ring-buffer mailbox (TX/RX channels to firmware)

### 5. habanalabs — Gaudi / Gaudi2

Intel's data-center AI accelerator family (formerly Habana Labs).

- `common/` — shared: command buffers, job submission, ASID management,
  context tracking, memory manager, device reset
- `gaudi/` — first-gen Gaudi (8x 100GbE, 32 TFLOPs)
- `gaudi2/` — second-gen Gaudi2 (24x 200GbE, 865 TFLOPs)
- `goya/` — Goya inference chip

Distinct from GPU drivers: no display, dedicated tensor engines, large
on-chip SRAM, RoCE networking fabric built in.

### 6. qaic — Qualcomm AI Cloud 100

- PCI device using **MHI** (Modem Host Interface) for control plane
- `sahara.c` — firmware boot protocol (Sahara loader)
- `qaic_data.c` — DMA channels for inference data
- `qaic_control.c` — control transactions (network setup)
- `qaic_ras.c` — RAS error reporting

---

## Job Submission Flow (ivpu example)

```
Userspace                  Kernel (ivpu)               NPU Firmware
    │                           │                           │
    │  ioctl(DRM_IVPU_SUBMIT)   │                           │
    ├──────────────────────────►│                           │
    │                           │  ivpu_job_submit()        │
    │                           │  ├─ validate cmdbufs      │
    │                           │  ├─ map GEM → IOMMU       │
    │                           │  └─ ivpu_cmdq_push_job()  │
    │                           │         │                 │
    │                           │  write doorbell ──────────►
    │                           │                           │  execute
    │                           │                           │  inference
    │                           │◄── JSM response ──────────┤
    │                           │  ivpu_job_done_cb()       │
    │                           │  drm_sched_job_done()     │
    │◄──────────────────────────┤                           │
    │  fence signaled           │                           │
```

---

## Memory Model

```
 ┌─────────────────────────────────────────────────────────┐
 │  Process VA space                                        │
 │  ┌─────────────┐   ┌─────────────┐   ┌───────────────┐  │
 │  │ Input tensor│   │ Output buf  │   │  Cmd buffer   │  │
 │  │ (HMM/shmem) │   │ (HMM/shmem) │   │  (device mem) │  │
 │  └──────┬──────┘   └──────┬──────┘   └───────┬───────┘  │
 └─────────┼────────────────┼────────────────────┼──────────┘
           │  GEM objects   │                    │
           ▼                ▼                    ▼
 ┌─────────────────────────────────────────────────────────┐
 │  NPU IOMMU (ivpu_mmu)                                   │
 │  SSID 0: global context (firmware)                      │
 │  SSID 2-65: per-user context (isolated VA spaces)       │
 └─────────────────────────────────────────────────────────┘
           │
           ▼ IOVA
 ┌─────────────────────────────────────────────────────────┐
 │  NPU hardware (VPU tiles, SHAVE DSPs, DMA engines)      │
 └─────────────────────────────────────────────────────────┘
```

---

## Key ioctls (ivpu)

| ioctl | Purpose |
|-------|---------|
| `DRM_IVPU_GET_PARAM` | Query device caps (clock, SKU, tile count) |
| `DRM_IVPU_SET_PARAM` | Set scheduling priority, power profile |
| `DRM_IVPU_BO_CREATE` | Allocate GEM buffer object |
| `DRM_IVPU_BO_INFO` | Get VA/IOVA/mmap offset for a BO |
| `DRM_IVPU_SUBMIT` | Submit a job (cmd buffer list) |
| `DRM_IVPU_BO_WAIT` | Wait for job fence completion |
| `DRM_IVPU_METRIC_STREAMER_START` | Start performance counter stream |

---

## Tracepoints

ivpu exposes tracepoints via `tracepoint:ivpu:*`:

| Tracepoint | Fires When |
|------------|-----------|
| `tracepoint:ivpu:pm` | Power state transition |
| `tracepoint:ivpu:job` | Job submitted / completed |
| `tracepoint:ivpu:jsm` | JSM firmware message sent/received |

---

## Files

```
drivers/accel/
├── drm_accel.c          accel core: class, chrdev, minor management
├── ivpu/                Intel NPU (Meteor/Arrow/Lunar/Panther Lake)
│   ├── ivpu_drv.c       PCI probe, file_priv, module params
│   ├── ivpu_fw.c        Firmware load and boot
│   ├── ivpu_ipc.c       IPC doorbell channel
│   ├── ivpu_job.c       Job/cmdq submission
│   ├── ivpu_gem.c       GEM allocator + IOMMU mapping
│   ├── ivpu_mmu.c       NPU internal MMU (SSID contexts)
│   ├── ivpu_pm.c        Runtime PM / D0i3
│   └── ivpu_trace.h     Tracepoints: pm / job / jsm
├── amdxdna/             AMD Ryzen AI NPU (XDNA / AIE2)
│   ├── amdxdna_pci_drv.c PCI probe
│   ├── aie2_ctx.c       AIE2 column context management
│   ├── aie2_solver.c    Column resource solver
│   ├── amdxdna_gem.c    GEM + HMM host memory
│   └── amdxdna_mailbox.c Mailbox ring-buffer to FW
├── habanalabs/          Intel Gaudi / Gaudi2 / Goya
│   ├── common/          Shared: CS, memory, context, reset
│   ├── gaudi/           Gaudi gen-1
│   ├── gaudi2/          Gaudi gen-2
│   └── goya/            Goya inference
└── qaic/                Qualcomm Cloud AI 100
    ├── qaic_drv.c       PCI + MHI setup
    ├── sahara.c         Firmware boot (Sahara protocol)
    ├── qaic_data.c      DMA inference channels
    └── qaic_ras.c       RAS error handling
```

---

## HackMD Export

Title: **Linux Kernel DRM Accelerator (accel) Subsystem**

To publish:
```bash
curl -X POST https://api.hackmd.io/v1/notes \
  -H "Authorization: Bearer $HACKMD_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"title\":\"Linux Kernel DRM Accelerator (accel) Subsystem\",\"content\":$(cat README.md | jq -Rs .)}"
```

---

## Test Cases

See [`accel_trace_test.py`](accel_trace_test.py) for bpftrace-based
step-by-step verification of the accel subsystem.
