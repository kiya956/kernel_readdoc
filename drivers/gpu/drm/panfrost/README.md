# DRM Panfrost — ARM Mali Midgard/Bifrost Driver

> **Source tree:** `drivers/gpu/drm/panfrost/`
> **Kernel:** noble-linux-oem
> **Date:** 2026-04-28
> **Scanned from:** ~/canonical/kernel/noble-linux-oem

---

## 1. Full Subsystem Stack

```
╔══════════════════════════════════════════════════════════════════════╗
║                       USER SPACE                                     ║
║  ┌──────────┐  ┌──────────┐  ┌──────────┐                          ║
║  │ Mesa     │  │ Vulkan   │  │ OpenCL   │                          ║
║  │ Panfrost │  │ PanVK    │  │ (rusticl)│                          ║
║  └────┬─────┘  └────┬─────┘  └────┬─────┘                          ║
║       └─────────────┴──────┬───────┘                                ║
║                            │  libdrm (SUBMIT / CREATE_BO / MMAP)     ║
╚════════════════════════════╪════════════════════════════════════════╝
                             │  ioctl
╔════════════════════════════╪════════════════════════════════════════╗
║  KERNEL — panfrost.ko (platform_driver, panfrost_drv.c:968)        ║
║  ┌─────────────────────────▼────────────────────────────────────┐  ║
║  │  panfrost_probe (panfrost_drv.c:723)                          │  ║
║  │    → panfrost_device_init (panfrost_device.c:200)             │  ║
║  │    → panfrost_gpu_init → panfrost_job_init → panfrost_mmu_init│  ║
║  └───────────────────────────┬──────────────────────────────────┘  ║
║                              │                                      ║
║  ┌───────────────────────────▼──────────────────────────────────┐  ║
║  │  panfrost_device (panfrost_device.h:125)                      │  ║
║  │  ┌──────────────────────────────────────────────────────┐    │  ║
║  │  │ ddev (*drm_device)     │ pdev (platform_device)      │    │  ║
║  │  │ iomem (void __iomem*)  │ clock / bus_clock           │    │  ║
║  │  │ gpu_irq / mmu_irq     │ features (panfrost_features) │    │  ║
║  │  │ js (*panfrost_job_slot)│ jobs[NUM_JOB_SLOTS][2]      │    │  ║
║  │  │ as_lock (spinlock)     │ as_in_use_mask / as_lru_list│    │  ║
║  │  │ scheduled_jobs (list)  │ devfreq (panfrost_devfreq)  │    │  ║
║  │  │ reset (drm_sched_reset)│ comp (panfrost_compatible)  │    │  ║
║  │  └──────────────────────────────────────────────────────┘    │  ║
║  └──────────────────────────────────────────────────────────────┘  ║
║                                                                      ║
║  ┌── Job submission (panfrost_job.c) ──────────────────────────┐    ║
║  │  panfrost_job_push (L297) → drm_sched_entity_push_job        │    ║
║  │  panfrost_job_run  (internal) → write to JS registers         │    ║
║  │  panfrost_job_irq_handler → fence signal on completion        │    ║
║  │  panfrost_job_timedout → GPU reset path                       │    ║
║  │  Uses drm_gpu_scheduler with NUM_JOB_SLOTS slots             │    ║
║  └──────────────────────────────────────────────────────────────┘    ║
║                                                                      ║
║  ┌── MMU (panfrost_mmu.c) ─────────────────────────────────────┐    ║
║  │  panfrost_mmu_map (L426) → map GEM BO into GPU address space  │    ║
║  │  panfrost_mmu_unmap (L452) → unmap from GPU AS                │    ║
║  │  panfrost_mmu_as_get → assign address space (up to 16 AS)     │    ║
║  │  panfrost_mmu_irq_handler → handle page faults                │    ║
║  │  Multi-level page table (4KB pages)                            │    ║
║  └──────────────────────────────────────────────────────────────┘    ║
║                                                                      ║
║  ┌── GEM (panfrost_gem.c) ─────────────────────────────────────┐    ║
║  │  panfrost_gem_object (panfrost_gem.h:52)                      │    ║
║  │  ┌───────────────────────────────────────────────────────┐   │    ║
║  │  │ base (drm_gem_shmem_object)  │ noexec (bool)          │   │    ║
║  │  │ is_heap (bool)               │ mappings (list)        │   │    ║
║  │  │ gpu_usecount (atomic_t)      │                        │   │    ║
║  │  └───────────────────────────────────────────────────────┘   │    ║
║  │  panfrost_gem_open (L149) → lazy GPU mapping                  │    ║
║  │  panfrost_gem_close (L204) → teardown mapping                 │    ║
║  │  panfrost_gem_shrinker.c → memory pressure reclaim            │    ║
║  └──────────────────────────────────────────────────────────────┘    ║
║                                                                      ║
║  ┌── GPU init / reset (panfrost_gpu.c) ────────────────────────┐    ║
║  │  panfrost_gpu_init → read GPU_ID, configure L2/tiler/shader  │    ║
║  │  panfrost_gpu_power_on → shader/tiler/L2 power domains       │    ║
║  │  panfrost_device_reset (panfrost_device.c:402) → full reset   │    ║
║  └──────────────────────────────────────────────────────────────┘    ║
╚════════════════════════════╪════════════════════════════════════════╝
                             │  MMIO / IRQ
╔════════════════════════════╪════════════════════════════════════════╗
║  HARDWARE — ARM Mali       ▼                                        ║
║  Midgard (T6xx/T7xx/T8xx) or Bifrost (G31/G51/G52/G71/G76)        ║
║  [ Shader cores ] [ Tiler ] [ L2 cache ] [ MMU (16 AS) ]           ║
║  [ Job Manager: 3 slots (vertex/tiler, fragment, compute) ]         ║
╚════════════════════════════════════════════════════════════════════╝
```

---

## 2. Component Details

### panfrost_device (panfrost_device.h:125)

Central driver state, embedded in `drm_device->dev_private`.

Key fields: `iomem`, `clock`/`bus_clock`, `regulators`, `rstc`, `features`,
`js` (job slots), `jobs[NUM_JOB_SLOTS][2]`, address space management (`as_lock`,
`as_in_use_mask`, `as_alloc_mask`, `as_faulty_mask`, `as_lru_list`).

### panfrost_gem_object (panfrost_gem.h:52)

Extends `drm_gem_shmem_object` with GPU-specific fields: `noexec` (non-executable),
`is_heap` (growable heap), `mappings` (list of `panfrost_gem_mapping`).

### Job Slots

Mali GPUs have a **Job Manager** with multiple job slots (typically 3):
- Slot 0: vertex/tiler jobs
- Slot 1: fragment jobs
- Slot 2: compute jobs

Each slot has 2 hardware entries (double-buffered). The driver uses
`drm_gpu_scheduler` for each slot.

---

## 3. Workflow: Job Submission

```
 Mesa (Panfrost)                Kernel                           Mali GPU
      │                            │                                │
      │  DRM_IOCTL_PANFROST_SUBMIT │                                │
      ├───────────────────────────►│                                │
      │                            │  panfrost_submit_ioctl         │
      │                            │  (panfrost_drv.c)              │
      │                            │                                │
      │                            │  1. Create panfrost_job        │
      │                            │     attach BOs, set JC addr    │
      │                            │                                │
      │                            │  2. panfrost_job_push (L297)   │
      │                            │     → drm_sched_entity_push_job│
      │                            │                                │
      │                            │  3. Scheduler picks job        │
      │                            │     → panfrost_job_run         │
      │                            │     → write JS_HEAD to MMIO    │
      │                            ├───────────────────────────────►│
      │                            │                                │
      │                            │  4. GPU executes job chain     │
      │                            │     IRQ on completion          │
      │                            │◄───────────────────────────────┤
      │                            │     panfrost_job_irq_handler   │
      │                            │     → dma_fence_signal         │
      │                            │                                │
      │◄── fence signals ─────────┤                                │
```

---

## 4. Key Source Files

| File | Purpose |
|---|---|
| `panfrost_drv.c` | Platform driver, ioctl table, module init (L968) |
| `panfrost_device.c` | Device init (L200)/fini (L289)/reset (L402) |
| `panfrost_device.h` | Core structs: `panfrost_device` (L125), `panfrost_mmu` (L184) |
| `panfrost_job.c` | Job submission: push (L297), put (L365), init (L837), IRQ handler |
| `panfrost_job.h` | `panfrost_job` struct |
| `panfrost_mmu.c` | GPU MMU: map (L426), unmap (L452), init (L895), IRQ handler |
| `panfrost_gem.c` | GEM: open (L149), close (L204), mapping management |
| `panfrost_gem.h` | `panfrost_gem_object` (L52), `panfrost_gem_mapping` (L106) |
| `panfrost_gpu.c` | GPU init, power on/off, feature detection |
| `panfrost_perfcnt.c` | Performance counter access |
| `panfrost_devfreq.c` | Dynamic frequency scaling |
| `panfrost_features.h` | Per-GPU feature/issue bitmaps |

---

## References

- `panfrost_device.h:125` — `struct panfrost_device`
- `panfrost_gem.h:52` — `struct panfrost_gem_object`
- `panfrost_drv.c:723` — `panfrost_probe`
- `panfrost_device.c:200` — `panfrost_device_init`
- `panfrost_job.c:297` — `panfrost_job_push`
- `panfrost_mmu.c:426` — `panfrost_mmu_map`
