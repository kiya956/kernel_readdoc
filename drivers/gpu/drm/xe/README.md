# Intel Xe GPU Driver — Deep Dive Analysis

> **Source tree:** `drivers/gpu/drm/xe/`
> **Kernel:** noble-linux-oem (oem-6.17-next)
> **Date:** 2026-04-18

---

## 1. Full Subsystem Stack

```
╔══════════════════════════════════════════════════════════════════════════════╗
║                           USER SPACE                                         ║
║  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌────────────────┐  ║
║  │  Mesa/ANV    │  │ Mesa/Iris/   │  │  VA-API /    │  │  Wayland /     ║
║  │  (Vulkan)    │  │ Crocus (GL)  │  │  MSDK(video) │  │  X11 compositor║
║  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └───────┬────────┘  ║
║         └─────────────────┴──────────────────┴──────────────────┘           ║
║                        │  libdrm + libdrm_xe   (ioctl wrappers)             ║
╚════════════════════════╪════════════════════════════════════════════════════╝
                          │  ioctl()
╔════════════════════════╪════════════════════════════════════════════════════╗
║  DRM CORE               ▼                                                    ║
║  drm_ioctl() ──► drm_ioctls[] ──► xe ioctl table  (12 ioctls)               ║
╚════════════════════════╪════════════════════════════════════════════════════╝
                          │
╔════════════════════════╪════════════════════════════════════════════════════╗
║  XE DRIVER              ▼                                                    ║
║                                                                               ║
║  ┌─────────────────────────────────────────────────────────────────────┐    ║
║  │                    xe_device  (root object)                         │    ║
║  │  ┌──────────────────────────────────────────────────────────────┐   │    ║
║  │  │  xe_tile[0]  (one tile = VRAM region + 1–2 GTs)              │   │    ║
║  │  │  ┌──────────────────────┐  ┌──────────────────────────────┐  │   │    ║
║  │  │  │  xe_gt (primary)     │  │  xe_gt (media, optional)     │  │   │    ║
║  │  │  │  ┌────────────────┐  │  │  ┌────────────────────────┐  │  │   │    ║
║  │  │  │  │  xe_hw_engine[]│  │  │  │  xe_hw_engine[] (VCS,  │  │  │   │    ║
║  │  │  │  │  RCS,BCS,CCS,  │  │  │  │  VECS, GSCCS)          │  │  │   │    ║
║  │  │  │  │  VECS0         │  │  │  └────────────────────────┘  │  │   │    ║
║  │  │  │  └────────────────┘  │  │  xe_uc (GuC/HuC/GSC fw)      │  │   │    ║
║  │  │  │  xe_uc (GuC/HuC fw)  │  └──────────────────────────────┘  │   │    ║
║  │  │  └──────────────────────┘                                     │   │    ║
║  │  │  xe_ggtt  (Global GTT, per tile)                              │   │    ║
║  │  └──────────────────────────────────────────────────────────────┘   │    ║
║  │  ┌─────────────┐  ┌──────────────┐  ┌────────────┐  ┌────────────┐  │    ║
║  │  │  TTM / VRAM │  │  xe_vm[]     │  │ xe_exec_   │  │  display/  │  │    ║
║  │  │  memory mgr │  │  (GPU VA)    │  │ queue[]    │  │  xe_display│  │    ║
║  │  └─────────────┘  └──────────────┘  └────────────┘  └────────────┘  │    ║
║  └─────────────────────────────────────────────────────────────────────┘    ║
╚══════════════════════════════════════════════════════════════════════════════╝
             │  PCIe MMIO (BAR0: 16 MB per tile) / DMA / MSI-X
╔══════════════════════════════════════════════════════════════════════════════╗
║  INTEL GPU HARDWARE (Xe architecture)                                        ║
║  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌────────────────┐  ║
║  │  GPC / EU    │  │  Copy Engine │  │  Video (VCS/ │  │  Display Engine║
║  │  (RCS / CCS) │  │  (BCS)       │  │  VECS/GSCCS) │  │  (DCE/DCN)     ║
║  └──────────────┘  └──────────────┘  └──────────────┘  └────────────────┘  ║
║  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────────────┐  ║
║  │  GuC / HuC   │  │  MMU / GGTT  │  │  VRAM (LPDDR/GDDR, tile-local)  │  ║
║  │  (firmware)  │  │  Page Tables │  │  + System RAM via GART           │  ║
║  └──────────────┘  └──────────────┘  └──────────────────────────────────┘  ║
╚══════════════════════════════════════════════════════════════════════════════╝
```

---

## 2. Directory Map

| Path | Purpose |
|---|---|
| `xe/` (root) | Driver core: device, tile, GT, engines, VM, exec, sync, PM |
| `xe/display/` | Display integration (DCE/DCN KMS, independent of i915) |
| `xe/regs/` | Per-generation register definitions |
| `xe/instructions/` | MI (memory interface) instruction encoding helpers |
| `xe/abi/` | ABI-stable structures shared with firmware |
| `xe/compat-i915-headers/` | Shim headers for shared i915/Xe display code |
| `xe/tests/` | In-kernel unit tests (KUnit) |

---

## 3. Layer-by-Layer Component Explanation

### Layer 0 — Hardware: Tile / GT / Engine hierarchy

Intel Xe splits a GPU into **tiles** (complete die with its own VRAM), each
containing one or two **GTs** (Graphics Tiles — functional units):

```
xe_device
  └─ xe_tile[0..N]   (one per physical die; N=0 for integrated GPUs)
       ├─ primary_gt  (XE_GT_TYPE_MAIN)  — render + compute + copy + display
       └─ media_gt   (XE_GT_TYPE_MEDIA) — present on media_ver >= 13 (Meteor Lake+)
```

Each **GT** owns a set of **hardware engines** discovered at init time:

| Engine ID | Class | Description |
|---|---|---|
| RCS0 | `XE_ENGINE_CLASS_RENDER` | 3D + compute (EU clusters) |
| BCS0–8 | `XE_ENGINE_CLASS_COPY` | Blitter / async DMA |
| VCS0–7 | `XE_ENGINE_CLASS_VIDEO_DECODE` | AV1/HEVC/H.264 decode |
| VECS0–3 | `XE_ENGINE_CLASS_VIDEO_ENHANCE` | Video post-processing |
| CCS0–3 | `XE_ENGINE_CLASS_COMPUTE` | Dedicated compute (Xe HPG+) |
| GSCCS0 | `XE_ENGINE_CLASS_OTHER` | Graphics Security Coprocessor |

---

### Layer 1 — xe_device (root struct)

`struct xe_device` (in `xe_device_types.h`) is the DRM device root:

```c
struct xe_device {
    struct drm_device   drm;          // embedded DRM device
    struct ttm_device   ttm;          // TTM memory manager

    struct xe_tile      tiles[XE_MAX_TILES_PER_DEVICE];  // max 2 tiles
    u8                  tile_count;

    struct {
        u32  platform;                // XE_TIGERLAKE, XE_ALDERLAKE, …
        u32  graphics_verx100;        // e.g. 1255 = Gen12.55
        u32  media_verx100;
        bool is_dgfx;                 // discrete GPU?
        u8   tile_count, max_gt_per_tile;
    } info;

    struct xe_ggtt     *ggtt;         // global GTT (shared)
    struct workqueue_struct *ordered_wq;
    struct workqueue_struct *unordered_wq;
    struct workqueue_struct *preempt_fence_wq;
};
```

---

### Layer 2 — xe_gt (Graphics Tile)

Each `struct xe_gt` (in `xe_gt_types.h`) owns engines and firmware:

```c
struct xe_gt {
    struct xe_tile     *tile;         // parent tile
    enum xe_gt_type     type;         // MAIN or MEDIA
    u8                  info.id;      // global GT id (0..3)
    u32                 info.engine_mask;  // bitmask of present engines

    struct xe_hw_engine hw_engines[XE_NUM_HW_ENGINES];

    struct xe_uc        uc;           // microcontroller hub (GuC/HuC/GSC)
    struct xe_force_wake force_wake;  // GT power-gating wakelock
    struct xe_gt_mcr    mcr;          // MCR (multicast register) steering
    spinlock_t          mmio_lock;
};
```

---

### Layer 3 — Execution Queue (xe_exec_queue)

Replaces i915's `intel_engine_cs` + `i915_gem_context` with a unified
per-user submission object:

```c
struct xe_exec_queue {
    struct xe_file         *xef;          // owning user context
    struct xe_gt           *gt;           // target GT
    struct xe_hw_engine    *hwe;          // target physical engine
    struct xe_vm           *vm;           // GPU virtual address space
    enum xe_engine_class    class;
    u16                     width;        // 1 = serial, >1 = parallel

    union {
        struct xe_guc_exec_queue    *guc;       // GuC submission state
        struct xe_execlist_exec_queue *execlist; // legacy execlist state
    };

    struct {
        u32  timeslice_us;
        u32  preempt_timeout_us;
        u32  priority;
    } sched_props;

    struct dma_fence *lr_pfence;          // long-running preempt fence
};
```

Created via `DRM_IOCTL_XE_EXEC_QUEUE_CREATE`; the user passes a
`drm_xe_engine_class_instance[]` array specifying the target engine.

---

### Layer 4 — Command Submission (xe_exec)

Xe uses the **VM_BIND model**: memory is bound to a GPU VA space
asynchronously *before* submission, so the exec ioctl carries only
batch-buffer addresses and fences — no per-submit BO list.

**xe_exec_ioctl() flow** (`xe_exec.c`):

```
xe_exec_ioctl()
  │
  ├─ 1. Parse drm_xe_exec (exec_queue_id, address, num_syncs, syncs[])
  │
  ├─ 2. Wait for pending async VM_BIND fences (if any)
  │
  ├─ 3. xe_vm_lock(vm, rd)    — read-lock the VM
  │
  ├─ 4. xe_userptr_validate_range()   — check pinned userpptrs still valid
  │
  ├─ 5. xe_bo_validate() per evicted BO → xe_vm_rebind() if needed
  │
  ├─ 6. xe_sched_job_create(eq, batch_addr)
  │      └─ wraps drm_sched_job
  │
  ├─ 7. xe_sync_entry_signal()  — hook out-fences / user fences
  │
  └─ 8. xe_sched_job_push()
         └─ drm_sched_entity_push_job()
              └─ xe_guc_submit_job() OR xe_execlist_submit_job()
                   └─ GPU reads LRC ring → executes batch
                        └─ engine interrupt → dma_fence_signal()
```

**Synchronization** — explicit only (no implicit BO syncing):

| Sync type | Value | Meaning |
|---|---|---|
| `DRM_XE_SYNC_TYPE_SYNCOBJ` | 0 | DRM syncobj (binary) |
| `DRM_XE_SYNC_TYPE_TIMELINE_SYNCOBJ` | 1 | DRM timeline syncobj |
| `DRM_XE_SYNC_TYPE_USER_FENCE` | 2 | Memory-mapped 64-bit fence value |

---

### Layer 5 — Virtual Memory: VM_BIND Model

`xe_vm` is a GPU address space created per-process (or per-context in Vulkan):

```
DRM_IOCTL_XE_VM_CREATE  →  xe_vm_create_ioctl()
  └─ allocates xe_vm with drm_gpuvm base

DRM_IOCTL_XE_VM_BIND   →  xe_vm_bind_ioctl()
  ├─ op=MAP:   insert xe_vma, walk page table, install PTEs (async)
  ├─ op=UNMAP: remove xe_vma, clear PTEs
  └─ returns out-fence: signals when PTEs are live on GPU

Page table hierarchy (4-level on 48-bit VA):
  L3 PD (root, GPU BO)
  └─ L2 PD
       └─ L1 PD
            └─ L0 PT  →  4 KiB PTE → physical VRAM or system RAM

Huge page support: 2 MiB (L1 bypass) and 1 GiB (L2 bypass)
```

**Fault mode** (`DRM_XE_VM_CREATE_FLAG_FAULT_MODE`): on supported platforms,
GPU page faults are caught and resolved on-demand (overcommit / sparse).

---

### Layer 6 — Memory Management (xe_bo + TTM)

```c
struct xe_bo {
    struct ttm_buffer_object ttbo;   // TTM base
    struct xe_device        *xe;
    u32                      flags;  // XE_BO_FLAG_VRAM0 | SYSTEM | GGTT | …
    struct iosys_map         vmap;   // CPU virtual mapping
    struct xe_vma_ops        ops;    // pending VM bind ops
};
```

**TTM placement types:**

| TTM placement | Flag | Physical location |
|---|---|---|
| `XE_PL_SYSTEM` | `XE_BO_FLAG_SYSTEM` | System RAM (uncached in GPU) |
| `XE_PL_TT` | TTM_PL_TT | System RAM via GGTT aperture |
| `XE_PL_VRAM0` | `XE_BO_FLAG_VRAM0` | Tile 0 VRAM (local to die) |
| `XE_PL_VRAM1` | `XE_BO_FLAG_VRAM1` | Tile 1 VRAM (dGPU second die) |
| `XE_PL_STOLEN` | `XE_BO_FLAG_STOLEN` | Pre-reserved stolen memory |

**GEM allocation** (`DRM_IOCTL_XE_GEM_CREATE → xe_gem_create_ioctl()`):
- `placement` is a bitmask of *memory region instance indices* from
  `DRM_XE_DEVICE_QUERY_MEM_REGIONS` — not hardcoded domain flags

**Eviction**: TTM shrinker calls `xe_bo_move()` → SDMA/BCS copy engine
moves data VRAM→system RAM, then updates all VMAs via `xe_vm_rebind()`.

---

### Layer 7 — GuC Firmware (xe_guc / xe_uc)

All modern Xe platforms use GuC submission (execlist is legacy fallback):

```
xe_uc (per GT)
  ├─ xe_guc    — Graphics Microcontroller (scheduling, power)
  ├─ xe_huc    — HEVC Codec Engine auth (optional)
  └─ xe_gsc    — Graphics Security Coprocessor (HDCP, PXP)

GuC initialization:
  xe_guc_init()
    ├─ xe_uc_fw_init()     — locate firmware in kernel firmware path
    ├─ xe_guc_ads_init()   — build ADS (Abstract Data Structure for GuC)
    └─ xe_guc_upload()     — DMA firmware binary to GPU WOPCM region
         └─ xe_guc_enable_communication()
              └─ CT (Command Transport) ring ready

Submission path:
  xe_guc_submit_job(job)
    └─ xe_guc_ct_send(H2G_TYPE_CTB_REQUEST)
         └─ GuC firmware: schedule LRC on hardware engine
              └─ engine interrupt → G2H completion message
                   └─ xe_guc_submit_done() → dma_fence_signal()
```

**Preemption**: GuC firmware initiates preemption independently; driver
sets a preempt fence that signals when the context is descheduled.

---

### Layer 8 — Display

`xe/display/` contains Xe's own display driver (independent of i915):

```
xe_display_init()
  └─ intel_display_driver_probe()   (shared logic from compat headers)
       ├─ intel_crtc_init()         — CRTC per pipe
       ├─ intel_plane_init()        — primary, cursor, sprite planes
       ├─ intel_connector_init()    — HDMI, DP, eDP connectors
       └─ intel_dp_init()           — DisplayPort link training

xe_fb_pin.c — framebuffer BO pinning for scanout
xe_hdcp_gsc.c — HDCP content protection via GSC firmware
xe_plane_initial.c — read BIOS-set plane state at boot
```

---

## 4. Data Flow Diagrams

### 4a. Full GPU Submission Path

```
 Mesa/Vulkan (userspace)           xe kernel              GuC FW      HW Engine
      │                                │                     │             │
      │  XE_VM_BIND (async map)        │                     │             │
      ├───────────────────────────────►│                     │             │
      │                                │ install PTEs async  │             │
      │◄── out-fence ──────────────────┤                     │             │
      │                                │                     │             │
      │  XE_EXEC (batch at GPU VA)     │                     │             │
      ├───────────────────────────────►│                     │             │
      │                                │ xe_sched_job_create │             │
      │                                │ attach in/out syncs │             │
      │                                │ xe_guc_ct_send()    │             │
      │                                ├────────────────────►│             │
      │                                │  H2G CTB request    │ schedule LRC│
      │                                │                     ├────────────►│
      │                                │                     │   execute   │
      │                                │                     │  G2H done   │
      │                                │◄────────────────────┤             │
      │                                │ dma_fence_signal()  │             │
      │◄── syncobj / user-fence ───────┤                     │             │
```

### 4b. VM_BIND Page Table Update

```
DRM_IOCTL_XE_VM_BIND (MAP)
  │
  ├─ xe_vm_bind_ioctl()
  │    └─ xe_vma_ops_add(MAP, bo, va, offset, size)
  │         └─ xe_pt_update_ops_prepare()  — walk 4-level PT, alloc PD BOs
  │              └─ xe_pt_update_ops_run() — write PTEs into page table BOs
  │                   └─ xe_vm_bind_ops_execute()
  │                        └─ submit bind job to internal bind queue
  │                             └─ bind fence signals when PTEs live on GPU
  │
  └─ out-fence returned to user (poll/syncobj wait)
```

### 4c. Device Initialization

```
xe_pci_probe()
  │
  ├─ xe_device_create()    — allocate xe_device + DRM device
  │
  ├─ xe_device_probe_early()
  │    ├─ detect tile count from hardware
  │    └─ xe_tile_mmio_init()  — map BAR0 regions per tile
  │
  └─ xe_device_probe()
       ├─ xe_gt_init_early()   — GT type, engine mask
       ├─ xe_ggtt_init()       — global GTT
       ├─ xe_irq_install()     — MSI-X IRQ routing
       ├─ xe_device_mem_access_get()
       ├─ xe_guc_init()        — GuC/HuC firmware load
       │    └─ xe_uc_init_hw() — WOPCM setup, firmware upload
       ├─ xe_hw_engines_init() — discover + init all HW engines
       ├─ xe_ttm_init()        — TTM VRAM/system memory managers
       └─ xe_display_init()    — DRM KMS / display driver
```

### 4d. Interrupt Handling

```
MSI-X IRQ fires
  └─ xe_irq_handler()              xe_irq.c
       ├─ xe_gt_irq_handler()
       │    ├─ engine interrupts → xe_hw_engine_irq_handler()
       │    │    └─ xe_guc_ct_irq_handler()  → parse G2H messages
       │    │         └─ xe_guc_submit_done() → dma_fence_signal()
       │    ├─ fault interrupts → xe_gt_pagefault_handler()
       │    └─ GT PM wakeref → xe_gt_pm_irq_handler()
       └─ xe_display_irq_handler() → drm_handle_vblank()
```

---

## 5. Key Source Files Quick Reference

### Core driver

| File | Purpose |
|---|---|
| `xe_pci.c` | PCI driver, `xe_pci_probe()`, device ID table |
| `xe_device.c` | `xe_device_create()`, `xe_device_probe()` |
| `xe_device_types.h` | `xe_device`, `xe_tile`, `xe_gt` structs |
| `xe_tile.c` | Tile MMIO / VRAM init |
| `xe_gt.c` | GT init, `xe_gt_init_early/hw/late()` |
| `xe_hw_engine.c` | Engine discovery + initialization |
| `xe_exec_queue.c` | Exec queue create/destroy, sched entity |
| `xe_exec.c` | `xe_exec_ioctl()` — main submission path |
| `xe_lrc.c` | Logical Ring Context alloc, ring writes |
| `xe_ring_ops.c` | Engine-specific ring command emission |
| `xe_sync.c` | Syncobj / user-fence integration |

### Memory

| File | Purpose |
|---|---|
| `xe_bo.c` | GEM/TTM buffer objects, `xe_bo_create_locked()` |
| `xe_vm.c` | VM create/destroy, `xe_vm_bind_ioctl()` |
| `xe_vma_ops.c` | VMA operation batching (map/unmap/rebind) |
| `xe_pt.c` | 4-level GPU page table walk and PTE installation |
| `xe_ggtt.c` | Global GTT, `xe_ggtt_node_insert()` |
| `xe_ttm_vram_mgr.c` | TTM VRAM placement manager |

### GuC / firmware

| File | Purpose |
|---|---|
| `xe_uc.c` | Microcontroller hub (`xe_uc_init/hw()`) |
| `xe_guc.c` | GuC init, ADS build, `xe_guc_upload()` |
| `xe_guc_submit.c` | GuC job submission, `xe_guc_submit_job()` |
| `xe_guc_ct.c` | CT (Command Transport) H2G/G2H ring |
| `xe_guc_ads.c` | Abstract Data Structure for GuC firmware |
| `xe_huc.c` | HuC HEVC codec engine auth |
| `xe_gsc.c` | GSC security coprocessor |

### Platform

| File | Purpose |
|---|---|
| `xe_query.c` | `DRM_IOCTL_XE_DEVICE_QUERY` handler |
| `xe_mmio.c` | MMIO read/write with force-wake |
| `xe_force_wake.c` | GT force-wake to prevent clock-gating stalls |
| `xe_irq.c` | MSI-X setup, IRQ routing |
| `xe_pm.c` | Runtime PM, D3 entry/exit |
| `display/xe_display.c` | KMS display integration |

---

## 6. IOCTL Surface

| IOCTL | Cmd | Purpose |
|---|---|---|
| `DRM_IOCTL_XE_DEVICE_QUERY` | 0x00 | Query engines, memory regions, GT list, config, topology |
| `DRM_IOCTL_XE_GEM_CREATE` | 0x01 | Allocate GPU buffer object (`xe_bo`) |
| `DRM_IOCTL_XE_GEM_MMAP_OFFSET` | 0x02 | Get fake mmap offset for CPU access |
| `DRM_IOCTL_XE_VM_CREATE` | 0x03 | Create GPU virtual address space |
| `DRM_IOCTL_XE_VM_DESTROY` | 0x04 | Destroy VM |
| `DRM_IOCTL_XE_VM_BIND` | 0x05 | Async map/unmap BO into GPU VA (returns fence) |
| `DRM_IOCTL_XE_EXEC_QUEUE_CREATE` | 0x06 | Create execution queue bound to engine + VM |
| `DRM_IOCTL_XE_EXEC_QUEUE_DESTROY` | 0x07 | Destroy execution queue |
| `DRM_IOCTL_XE_EXEC_QUEUE_GET_PROPERTY` | 0x08 | Query queue property (ban status, etc.) |
| `DRM_IOCTL_XE_EXEC` | 0x09 | Submit batch buffer(s) with explicit fences |
| `DRM_IOCTL_XE_WAIT_USER_FENCE` | 0x0a | Blocking wait on GPU-written memory value |
| `DRM_IOCTL_XE_OBSERVATION` | 0x0b | OA (Observability / perf monitoring) stream |

All IOCTLs support a forward-compatible `extensions` pointer chain
(`struct drm_xe_user_extension`).

---

## 7. Xe vs i915: Key Differences

| Aspect | i915 | Xe |
|---|---|---|
| Memory binding | Implicit (per-execbuf BO list) | Explicit async VM_BIND |
| Synchronization | Implicit BO tracking + fences | Explicit syncobjs / user-fences only |
| Submission API | `GEM_EXECBUFFER2` (batch + BO list) | `XE_EXEC` (batch addr + fences) |
| Engine model | `intel_engine_cs` (class + instance) | `xe_exec_queue` (width-N virtual queue) |
| Multi-tile | Bolted on (multi-GT in one drm_device) | Native `xe_tile[]` hierarchy |
| GuC firmware | Optional, loaded via `xe_guc_ct` | Mandatory on all modern platforms |
| Display | `intel_display` (entangled with render) | `display/` subdirectory (cleaner) |
| VM address bits | 48-bit (PPGTT) | 48–57 bit (platform-dependent) |

---

## 8. Supported Platforms

| Platform | `info.platform` | Notes |
|---|---|---|
| Tiger Lake (TGL) | `XE_TIGERLAKE` | First Xe arch (Gen12 LP) |
| Rocket Lake (RKL) | `XE_ROCKETLAKE` | Desktop Gen12 |
| Alder Lake S/P/N | `XE_ALDERLAKE_*` | Hybrid core (P+E) |
| Raptor Lake P/S/U | `XE_RAPTOLAKE_*` | Gen13 refresh |
| DG1 | `XE_DG1` | First discrete Xe card |
| DG2 / ATS-M | `XE_DG2` | Xe HPG (ACM / Arc A-series) |
| Meteor Lake | `XE_METEORLAKE` | First Xe-LPM+ + media GT split |
| Battlemage (BMG) | `XE_BATTLEMAGE` | Xe2 HPG (Arc B-series) |

---

## References

- `drivers/gpu/drm/xe/xe_pci.c` — PCI driver, device ID table
- `drivers/gpu/drm/xe/xe_device.c` — `xe_device_probe()`
- `drivers/gpu/drm/xe/xe_exec.c` — `xe_exec_ioctl()` submission path
- `drivers/gpu/drm/xe/xe_vm.c` — `xe_vm_bind_ioctl()` VM_BIND
- `drivers/gpu/drm/xe/xe_guc_submit.c` — GuC job submission
- `drivers/gpu/drm/xe/xe_guc_ct.c` — CT H2G/G2H transport
- `drivers/gpu/drm/xe/xe_lrc.c` — LRC ring management
- `drivers/gpu/drm/xe/xe_pt.c` — GPU page table walker
- `include/uapi/drm/xe_drm.h` — UAPI ioctl definitions
