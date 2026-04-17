# i915 Driver — Deep Dive Analysis

> **Source tree:** `drivers/gpu/drm/i915/`
> **Kernel:** noble-linux-oem (oem-6.17-next)
> **Date:** 2026-04-17

---

## 1. Full Subsystem Stack

```
╔══════════════════════════════════════════════════════════════════════╗
║                        USER SPACE                                    ║
║  ┌────────────┐  ┌────────────┐  ┌───────────────┐  ┌────────────┐  ║
║  │  Mesa/ANV  │  │ Mesa/Iris  │  │  VA-API / MFX │  │  Wayland   ║
║  │  (Vulkan)  │  │ (OpenGL)   │  │  (video codec)│  │ compositor │  ║
║  └─────┬──────┘  └─────┬──────┘  └───────┬───────┘  └─────┬──────┘  ║
║        └───────────────┴─────────────────┴────────────────┘         ║
║                                   │ libdrm  (ioctl wrappers)        ║
╚═══════════════════════════════════╪════════════════════════════════╝
                                    │  ioctl()
╔═══════════════════════════════════╪════════════════════════════════╗
║  DRM CORE                         ▼                                 ║
║  drm_ioctl() ──► drm_ioctls[] ──► i915 ioctl table                 ║
╚═══════════════════════════════════╪════════════════════════════════╝
                                    │
╔═══════════════════════════════════╪════════════════════════════════╗
║  i915 DRIVER                      ▼                                 ║
║                                                                      ║
║  ┌─────────────────────────────────────────────────────────────┐    ║
║  │                 drm_i915_private  (root object)             │    ║
║  │   ┌──────────┐  ┌──────────────┐  ┌──────────────────────┐ │    ║
║  │   │  display │  │  intel_gt[0] │  │  intel_gt[1] (multi) │ │    ║
║  │   │  (KMS)   │  │  (primary)   │  │  (Media/Compute tile)│ │    ║
║  │   └────┬─────┘  └──────┬───────┘  └──────────────────────┘ │    ║
║  └────────┼───────────────┼─────────────────────────────────────┘   ║
║           │               │                                          ║
║     ┌─────▼─────┐   ┌─────▼──────────────────────────────────┐     ║
║     │ intel_    │   │              intel_gt                   │     ║
║     │ display   │   │  ┌──────────┐  ┌────────┐  ┌────────┐  │     ║
║     │ (CRTC /   │   │  │intel_uc  │  │ i915_  │  │intel_  │  │     ║
║     │  planes / │   │  │ GuC/HuC/ │  │ ggtt   │  │uncore  │  │     ║
║     │  connectors│  │  │ GSC fw   │  │ (GTT)  │  │ (MMIO) │  │     ║
║     └───────────┘   │  └────┬─────┘  └────────┘  └────────┘  │     ║
║                     │       │  engines[]                      │     ║
║                     │  ┌────▼─────────────────────────────┐   │     ║
║                     │  │  intel_engine_cs  (per engine)   │   │     ║
║                     │  │  RCS  BCS  VCS  VECS  CCS        │   │     ║
║                     │  │  execlists | GuC submission port  │   │     ║
║                     │  └──────────────────────────────────┘   │     ║
║                     └────────────────────────────────────────┘     ║
╚══════════════════════════════════════════════════════════════════════╝
                              │  PCIe MMIO / DMA / IRQ
╔══════════════════════════════════════════════════════════════════════╗
║  HARDWARE (Intel GPU)                                                ║
║  ┌──────────┐  ┌──────────┐  ┌──────────────┐  ┌────────────────┐  ║
║  │  Render  │  │  Blitter │  │  Video (VCS) │  │ Display Engine │  ║
║  │  Engine  │  │ (BCS/CCS)│  │  VECS codec  │  │  Pipes/Planes  │  ║
║  └──────────┘  └──────────┘  └──────────────┘  └────────────────┘  ║
║  ┌──────────────────────────────────────────┐  ┌────────────────┐  ║
║  │  PPGTT (per-process page tables in HW)   │  │    VRAM/LMEM   │  ║
║  └──────────────────────────────────────────┘  └────────────────┘  ║
╚══════════════════════════════════════════════════════════════════════╝
```

---

## 2. Layer-by-Layer Component Explanation

### Layer 0 — Hardware

| Component | Role |
|---|---|
| Render Engine (RCS) | 3D pipeline, compute shaders |
| Blitter Engine (BCS) | Fast memory copy / fill |
| Video Engine (VCS) | H.264/HEVC/AV1 encode/decode |
| Video Enhancement (VECS) | Post-processing, scaling |
| Compute Engine (CCS) | Gen12+ dedicated compute |
| Display Engine | CRTC, planes, HDMI/DP PHY, audio |
| GGTT | 4 GB global aperture (CPU-visible) |
| PPGTT / LMEM | Per-process address space + local VRAM |

---

### Layer 1 — intel_uncore (MMIO abstraction)

Every register read/write on Intel GPUs goes through `intel_uncore`:

```
intel_uncore_read(uncore, reg)
  │
  ├─ forcewake_get()  — wake GT from RC6 sleep
  ├─ readl(uncore->regs + offset(reg))
  └─ forcewake_put()  — allow sleep again
```

Multi-tile systems have one `intel_uncore` per tile (GT).

---

### Layer 2 — intel_gt (Graphics Tile)

Central hub for one GPU tile:

```c
struct intel_gt {
    struct drm_i915_private *i915;    // back-pointer
    struct intel_uncore     *uncore;  // MMIO ops
    struct i915_ggtt        *ggtt;    // global GTT
    struct intel_uc          uc;      // GuC + HuC + GSC
    struct intel_wopcm       wopcm;   // GuC/HuC memory region
    struct intel_reset        reset;  // GPU hang recovery
    struct intel_gt_timelines timelines; // active timeline list
    struct intel_gt_requests  requests;  // retire_work timer
    struct intel_wakeref      wakeref;   // PM runtime ref
    /* engine[] — populated during driver init */
};
```

---

### Layer 3 — intel_engine_cs (one per HW engine)

Each GPU engine is represented by `intel_engine_cs`:

```c
struct intel_engine_cs {
    struct intel_gt           *gt;
    u8                         class, instance;  // e.g. RCS=0, BCS=1
    intel_engine_mask_t        mask;
    u32                        mmio_base;        // engine register base

    /* Submission back-end (one of two modes): */
    struct intel_engine_execlists  execlists;    // legacy ExecLists
    /* — OR — */
    /* GuC submission (intel_guc_submission.c) */

    struct intel_context      *kernel_context;  // i915 internal use
    struct i915_request       *heartbeat;       // hang detection
    struct intel_ring         *legacy_active_ring;
};
```

**Two submission modes:**

| Mode | When | How |
|---|---|---|
| ExecLists (ELSP) | Gen8–Gen11, GuC disabled | Driver writes LRC descriptors directly to ELSP register |
| GuC submission | Gen12+ (default) | Driver sends H2G CT message; GuC schedules on HW |

---

### Layer 4 — GEM / execbuffer (userspace request path)

```
i915_gem_execbuffer2_ioctl()
  │
  └─ i915_gem_do_execbuffer()
       │
       ├─ 1. Parse exec_objects[]  →  resolve GEM handles → drm_i915_gem_object
       ├─ 2. eb_lookup_vmas()      →  find/create VMA per object
       ├─ 3. eb_reserve()          →  pin VMAs into PPGTT (bind pages)
       ├─ 4. eb_relocate()         →  patch GPU-VA references in batch
       ├─ 5. i915_request_create() →  allocate i915_request on engine timeline
       ├─ 6. emit_bb_start()       →  write MI_BATCH_BUFFER_START into ring
       ├─ 7. i915_request_add()    →  submit to engine (execlists or GuC)
       └─ 8. Return fence fd       →  caller polls for completion
```

---

### Layer 5 — PPGTT (Per-Process GTT)

Each `i915_gem_context` has an `i915_address_space` (VM):

```
i915_gem_context
  └─ i915_address_space (ppgtt)
       ├─ 48-bit VA space (4-level page tables on Gen8+)
       ├─ drm_mm  range allocator  →  VMA placement
       └─ insert_entries() / clear_range()  →  GPU page table writes
```

Hardware walks GPU page tables independently of CPU MMU — PPGTT provides per-process isolation on the GPU.

---

### Layer 6 — GuC / HuC / GSC Firmware

```
intel_uc
  ├─ intel_guc   — workload scheduling + SLPC power management
  │    ├─ intel_guc_ct   — CT (Command Transport) H2G/G2H ring
  │    ├─ intel_guc_submission — convert i915_request → GuC work item
  │    └─ intel_guc_slpc — dynamic freq/power via GuC
  ├─ intel_huc   — content protection (DRM decode auth)
  └─ intel_gsc   — MEI/HECI proxy for platform security controller
```

GuC CT message flow:
```
i915_request_add()
  └─ intel_guc_submit()
       └─ ct_send(H2G_TYPE_SCHED_CONTEXT_MODE_SET)
            └─ GuC firmware schedules on HW engine
                 └─ completion IRQ → G2H message → dma_fence_signal()
```

---

### Layer 7 — Display (intel_display)

Separate subsystem inside i915, owns KMS objects:

```
intel_display
  ├─ intel_crtc[]       (timing generators)
  ├─ intel_plane[]      (primary, sprite, cursor)
  ├─ intel_connector[]  (HDMI, DP, eDP, VGA)
  ├─ intel_encoder[]    (DDI, DSI, CRT)
  └─ intel_cdclk        (display clock management)
```

Atomic commit path:
```
intel_atomic_commit()
  ├─ intel_atomic_check()     — validate pipe bandwidth, clocks
  ├─ intel_atomic_prepare_commit()
  │    └─ intel_prepare_plane_fb() — pin framebuffer
  └─ intel_atomic_commit_tail()
       ├─ intel_update_crtc()  — program display registers
       └─ intel_wait_for_vblank() → drm_handle_vblank()
```

---

## 3. Data Flow Diagrams

### 3a. GPU Command Submission (GuC mode)

```
 Mesa (userspace)              i915 kernel              GuC FW         HW Engine
     │                              │                      │               │
     │  execbuffer2 ioctl           │                      │               │
     ├─────────────────────────────►│                      │               │
     │                              │ pin VMAs in PPGTT    │               │
     │                              │ create i915_request  │               │
     │                              │ emit BB_START → ring │               │
     │                              │ guc_submit()         │               │
     │                              ├─────────────────────►│               │
     │                              │   H2G CT message     │               │
     │                              │                      │ schedule LRC  │
     │                              │                      ├──────────────►│
     │                              │                      │   executes    │
     │                              │                      │  G2H done msg │
     │                              │◄─────────────────────┤               │
     │                              │ dma_fence_signal()   │               │
     │◄── sync_file / fence ────────┤                      │               │
```

### 3b. ExecLists submission (legacy, Gen8–11)

```
 i915_request_add()
   └─ execlists_submit_request()
        └─ queue request in engine->execlists.queue (priority rbtree)
             └─ execlists_submission_tasklet()
                  └─ write LRC descriptor pair to ELSP register
                       └─ HW preempts/runs contexts
                            └─ CSB interrupt → retire requests
```

### 3c. GPU Hang Detection & Reset

```
intel_engine_cs.heartbeat_work
  │
  ├─ emit heartbeat request every N ms
  │
  ├─ if heartbeat not retired → engine stalled
  │
  └─ intel_gt_reset()
       ├─ intel_engine_reset()   — per-engine reset (Gen8+)
       └─ intel_gt_reset_global() — full GT reset (fallback)
            └─ i915_reset_error_state() — capture GPU error state
                 └─ /sys/class/drm/card0/error  (user-readable dump)
```

### 3d. LMEM (Local Memory) Object Lifecycle

```
i915_gem_object_create_lmem()
  ├─ intel_memory_region_create_obj()   — allocate LMEM pages
  ├─ __i915_gem_object_set_pages()      — bind struct pages
  └─ i915_vma_pin() → ppgtt insert_entries()  — map in GPU VA space

On eviction:
  i915_gem_object_migrate()
  ├─ blt_copy_object()     — LMEM → SMEM via blitter engine
  └─ unmap old LMEM pages  — free for other objects
```

---

## 4. Key Source Files Quick Reference

| File | Purpose |
|---|---|
| `i915_driver.c` | PCI probe/remove, drm_driver registration |
| `i915_drv.h` | `drm_i915_private` root struct |
| `gt/intel_gt.c` | GT init/exit, tile management |
| `gt/intel_engine_cs.c` | Engine discovery, class/instance mapping |
| `gt/intel_execlists_submission.c` | ExecLists port scheduling |
| `gt/uc/intel_guc_submission.c` | GuC-based scheduling |
| `gt/uc/intel_guc_ct.c` | GuC H2G/G2H command transport |
| `gt/uc/intel_guc_slpc.c` | Single Loop Power Control (freq policy) |
| `gem/i915_gem_execbuffer.c` | Userspace batch submission |
| `gem/i915_gem_context.c` | GEM context / PPGTT lifecycle |
| `gem/i915_gem_mman.c` | GEM mmap (fault handler, WC/WB) |
| `i915_gem_gtt.c` | GGTT management |
| `gt/gen8_ppgtt.c` | 4-level PPGTT page table ops |
| `i915_irq.c` | IRQ setup, GT/display interrupt dispatch |
| `i915_gpu_error.c` | Hang detection, error state capture |
| `display/` | intel_display, crtc, plane, connector, DDI |

---

## 5. i915 IOCTL Surface

| IOCTL | Handler | Purpose |
|---|---|---|
| `GEM_CREATE` | `i915_gem_create_ioctl` | Allocate SMEM GEM object |
| `GEM_MMAP` | `i915_gem_mmap_ioctl` | Map object into userspace |
| `GEM_EXECBUFFER2` | `i915_gem_execbuffer2_ioctl` | Submit GPU command batch |
| `GEM_BUSY` | `i915_gem_busy_ioctl` | Poll object fence state |
| `GEM_WAIT` | `i915_gem_wait_ioctl` | Wait for object idle |
| `GEM_CONTEXT_CREATE` | `i915_gem_context_create_ioctl` | Create GPU context/PPGTT |
| `GEM_SET_DOMAIN` | `i915_gem_set_domain_ioctl` | CPU cache coherency |
| `GET_PARAM` | `i915_getparam_ioctl` | Query driver capabilities |
| `PERF_OPEN` | `i915_perf_open_ioctl` | OA unit performance counters |
| `QUERY` | `i915_query_ioctl` | Topology, memory regions, etc. |

---

## 6. Power Management Summary

```
RC6 (Render C6) — engine idle → GT clock/power gate
  intel_rc6_enable()
    └─ GT_PM_IER / RC6_THRESHOLD registers

SLPC (GuC Single Loop Power Control)
  intel_guc_slpc_set_min/max_freq()
    └─ H2G SLPC message → GuC adjusts P-state

Runtime PM
  intel_runtime_pm_get() / _put()
    └─ pci_disable_link_state()
    └─ forcewake reference count
```

---

## References

- `drivers/gpu/drm/i915/i915_driver.c` — `i915_driver_probe`
- `drivers/gpu/drm/i915/gt/intel_gt.c` — `intel_gt_init`
- `drivers/gpu/drm/i915/gt/intel_engine_cs.c` — engine discovery
- `drivers/gpu/drm/i915/gem/i915_gem_execbuffer.c` — submission path
- `drivers/gpu/drm/i915/gt/uc/intel_guc_submission.c` — GuC submission
- `drivers/gpu/drm/i915/gt/uc/intel_guc_ct.c` — CT transport
- Documentation: `Documentation/gpu/i915.rst`
