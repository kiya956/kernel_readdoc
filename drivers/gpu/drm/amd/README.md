# AMD GPU Driver (amdgpu) — Deep Dive Analysis

> **Source tree:** `drivers/gpu/drm/amd/`
> **Kernel:** noble-linux-oem (oem-6.17-next)
> **Date:** 2026-04-17

---

## 1. Full Subsystem Stack

```
╔══════════════════════════════════════════════════════════════════════════════╗
║                            USER SPACE                                        ║
║  ┌──────────────┐  ┌──────────────┐  ┌───────────────┐  ┌────────────────┐  ║
║  │  Mesa/RADV   │  │ Mesa/RadeonSI│  │  VCN / VA-API │  │  Wayland /     ║
║  │  (Vulkan)    │  │  (OpenGL)    │  │  (video codec)│  │  X11 compositor│  ║
║  └──────┬───────┘  └──────┬───────┘  └───────┬───────┘  └───────┬────────┘  ║
║         └─────────────────┴─────────────────┴───────────────────┘           ║
║                                  │ libdrm_amdgpu  (ioctl wrappers)          ║
║                                  │                                           ║
║  ┌───────────────────────────────────────────────────────────────────────┐   ║
║  │  ROCm / HSA / HIP  (GPU compute runtime)                             │   ║
║  │   hsa-runtime → /dev/kfd  (KFD char device)                         │   ║
║  └───────────────────────────────┬───────────────────────────────────────┘   ║
╚═════════════════════════════════╪═══════════════════════════════════════════╝
                                   │  ioctl()  /  mmap()
╔═════════════════════════════════╪═══════════════════════════════════════════╗
║  DRM CORE                        ▼                                           ║
║  drm_ioctl() ──► drm_ioctls[] ──► amdgpu ioctl table                        ║
╚═════════════════════════════════╪═══════════════════════════════════════════╝
                                   │
╔═════════════════════════════════╪═══════════════════════════════════════════╗
║  AMDGPU DRIVER                   ▼                                           ║
║                                                                               ║
║  ┌────────────────────────────────────────────────────────────────────────┐  ║
║  │                   amdgpu_device  (root object)                        │  ║
║  │                                                                        │  ║
║  │  ┌──────────────┐  ┌──────────────┐  ┌───────────┐  ┌─────────────┐  │  ║
║  │  │  IP Blocks   │  │  Rings[]     │  │  VM / GTT │  │  IRQ handler│  │  ║
║  │  │  (GFX, GMC,  │  │  (GFX ring,  │  │  amdgpu_  │  │  amdgpu_   │  │  ║
║  │  │  IH, SMC,    │  │  SDMA ring,  │  │  vm.c     │  │  irq.c     │  │  ║
║  │  │  PSP, VCN,   │  │  KIQ, MES…)  │  │           │  │             │  │  ║
║  │  │  JPEG, DCN)  │  └──────────────┘  └───────────┘  └─────────────┘  │  ║
║  │  └──────────────┘                                                      │  ║
║  │  ┌──────────────┐  ┌──────────────┐  ┌───────────┐  ┌─────────────┐  │  ║
║  │  │  Command     │  │  Memory Mgr  │  │  PM/DPM   │  │  Display DC │  │  ║
║  │  │  Submission  │  │  TTM + GEM   │  │  swsmu /  │  │  amdgpu_dm  │  │  ║
║  │  │  amdgpu_cs.c │  │  amdgpu_ttm  │  │  powerplay│  │  + dc/      │  │  ║
║  │  └──────────────┘  └──────────────┘  └───────────┘  └─────────────┘  │  ║
║  └────────────────────────────────────────────────────────────────────────┘  ║
║                                                                               ║
║  ┌──────────────────────────────────────────────────────────────────────┐    ║
║  │  amdkfd  (Kernel Fusion Driver — /dev/kfd)                          │    ║
║  │  Process/queue management for HSA/OpenCL/HIP                        │    ║
║  └──────────────────────────────────────────────────────────────────────┘    ║
╚═══════════════════════════════════════════════════════════════════════════════╝
                     │  PCIe MMIO / DMA / IRQ / AXI
╔═══════════════════════════════════════════════════════════════════════════════╗
║  AMD GPU HARDWARE                                                             ║
║  ┌──────────────┐  ┌──────────────┐  ┌────────────────┐  ┌───────────────┐  ║
║  │  GFX Engine  │  │  SDMA Engine │  │   VCN (Video)  │  │  Display (DCN)║
║  │  Compute CU  │  │  (DMA copy)  │  │  Decode/Encode │  │  Pipes/Planes │  ║
║  └──────────────┘  └──────────────┘  └────────────────┘  └───────────────┘  ║
║  ┌──────────────┐  ┌──────────────┐  ┌────────────────┐  ┌───────────────┐  ║
║  │  GMC (Memory │  │  IH (Interrupt│  │  PSP (Platform │  │   SMU / SMC   ║
║  │  Controller) │  │  Handler)    │  │  Security Proc)│  │  (Power Ctrl) │  ║
║  └──────────────┘  └──────────────┘  └────────────────┘  └───────────────┘  ║
╚═══════════════════════════════════════════════════════════════════════════════╝
```

---

## 2. Directory Map

| Directory | Purpose |
|---|---|
| `amdgpu/` | Core GPU driver: device init, command submission, memory, scheduling |
| `amdkfd/` | Kernel Fusion Driver for heterogeneous computing (HSA/ROCm/HIP) |
| `display/` | Display Core (DC/DAL) — KMS/atomic modesetting, DisplayPort, HDMI |
| `pm/` | Power management: swsmu, powerplay, legacy-dpm, SMU interface |
| `acp/` | Audio Co-Processor driver |
| `amdxcp/` | Cross-Core Partitioning for multi-partition GPUs |
| `include/` | Shared headers, register definitions, IP interface contracts |

---

## 3. Layer-by-Layer Component Explanation

### Layer 0 — Hardware IP Blocks

AMD GPUs are built from versioned hardware IP blocks. Each block has an independent implementation per generation:

| IP Block | Type Enum | Role |
|---|---|---|
| Common | `AMD_IP_BLOCK_TYPE_COMMON` | ASIC family detection, early MMIO |
| GMC | `AMD_IP_BLOCK_TYPE_GMC` | Graphics Memory Controller, GART, page tables |
| IH | `AMD_IP_BLOCK_TYPE_IH` | Interrupt Handler ring buffer |
| PSP | `AMD_IP_BLOCK_TYPE_PSP` | Platform Security Processor (firmware auth) |
| SMC/SMU | `AMD_IP_BLOCK_TYPE_SMC` | System Management Controller (clock/power) |
| GFX | `AMD_IP_BLOCK_TYPE_GFX` | 3D + Compute shader engines |
| SDMA | `AMD_IP_BLOCK_TYPE_SDMA` | Scatter-Gather DMA engines |
| DCE/DCN | `AMD_IP_BLOCK_TYPE_DCE` | Display (legacy DCE → modern DCN) |
| VCN | `AMD_IP_BLOCK_TYPE_VCN` | Unified Video Codec Next (decode+encode) |
| MES | `AMD_IP_BLOCK_TYPE_MES` | Micro-Engine Scheduler (RDNA3+) |
| JPEG | `AMD_IP_BLOCK_TYPE_JPEG` | Dedicated JPEG decode engine |
| VPE | `AMD_IP_BLOCK_TYPE_VPE` | Video Processing Engine |

Each block implements the `amd_ip_funcs` vtable:

```c
struct amd_ip_funcs {
    int  (*early_init)(void *handle);   // Detect hardware, set caps
    int  (*sw_init)(void *handle);      // Alloc software resources
    int  (*hw_init)(void *handle);      // Program hardware registers
    int  (*hw_fini)(void *handle);      // Stop hardware
    int  (*sw_fini)(void *handle);      // Free software resources
    int  (*suspend)(void *handle);
    int  (*resume)(void *handle);
    bool (*is_idle)(void *handle);
    int  (*soft_reset)(void *handle);
    int  (*set_clockgating_state)(void *handle, enum amd_clockgating_state);
    int  (*set_powergating_state)(void *handle, enum amd_powergating_state);
};
```

---

### Layer 1 — amdgpu_device (Root Object)

`struct amdgpu_device` (defined in `amdgpu/amdgpu.h`) is the central object:

```c
struct amdgpu_device {
    struct drm_device     dev;           // embedded DRM device
    struct pci_dev       *pdev;          // PCI device handle

    /* IP blocks */
    struct amdgpu_ip_block  ip_blocks[AMDGPU_MAX_IP_NUM];
    int                     num_ip_blocks;

    /* Ring buffers */
    struct amdgpu_ring     *rings[AMDGPU_MAX_RINGS];
    int                     num_rings;

    /* Memory management */
    struct amdgpu_gmc       gmc;         // memory controller
    struct amdgpu_gart      gart;        // GART (CPU-visible aperture)
    struct ttm_device       mman;        // TTM buffer manager

    /* Virtual memory */
    struct amdgpu_vm_manager vm_manager; // VMID allocation, page table pool

    /* Power management */
    struct amdgpu_pm        pm;
    struct smu_context      smu;         // SMU (swsmu path)

    /* Interrupt handling */
    struct amdgpu_irq_src   irq;
    struct amdgpu_ih        irq_ih, irq_ih1, irq_ih2;

    /* Display */
    struct amdgpu_display_manager dm;    // DRM KMS ↔ DC bridge

    /* KFD integration */
    struct kgd_dev         *kfd;
};
```

**Initialization order** (`amdgpu_device_init`, `amdgpu/amdgpu_device.c`):

```
pci_probe → amdgpu_device_init()
  │
  ├─ 1. MMIO map (PCI BAR5 / BAR2)
  ├─ 2. amdgpu_device_ip_early_init()   — discover & register IP blocks
  ├─ 3. amdgpu_ttm_init()               — TTM memory manager
  ├─ 4. amdgpu_device_ip_init()
  │      ├─ foreach ip_block: sw_init()
  │      └─ foreach ip_block: hw_init()
  ├─ 5. amdgpu_device_ip_late_init()    — post-hw init hooks
  └─ 6. drm_dev_register()              — expose /dev/dri/card0
```

---

### Layer 2 — Ring Buffers (Command Submission Queues)

Each hardware engine exposes one or more **ring buffers**:

```c
struct amdgpu_ring {
    struct amdgpu_device   *adev;
    const struct amdgpu_ring_funcs *funcs;  // emit_ib, emit_fence, pad_ib...

    struct amdgpu_bo       *ring_obj;   // GPU BO for ring storage
    volatile uint32_t      *ring;       // CPU-mapped ring buffer
    uint64_t                gpu_addr;   // GPU virtual address of ring

    uint32_t                wptr;       // write pointer (CPU advances)
    uint32_t                rptr;       // read pointer (GPU advances)
    uint32_t                ring_size;  // size in DWORDs
    uint32_t                count_dw;   // free space in DWORDs

    enum amdgpu_ring_type   funcs_type; // GFX / COMPUTE / SDMA / VCN…
    struct drm_gpu_scheduler sched;     // DRM GPU scheduler
    struct amdgpu_fence_driver fence_drv; // fence tracking
};
```

**Ring types:**

| Ring Type | Usage |
|---|---|
| `AMDGPU_RING_TYPE_GFX` | 3D draw calls, graphics pipeline |
| `AMDGPU_RING_TYPE_COMPUTE` | Compute dispatches (ACE queues) |
| `AMDGPU_RING_TYPE_SDMA` | DMA copy/fill operations |
| `AMDGPU_RING_TYPE_VCN_DEC` | Video decode |
| `AMDGPU_RING_TYPE_VCN_ENC` | Video encode |
| `AMDGPU_RING_TYPE_VCN_JPEG` | JPEG decode |
| `AMDGPU_RING_TYPE_KIQ` | Kernel Interface Queue (MEC control) |
| `AMDGPU_RING_TYPE_MES` | Micro-Engine Scheduler ring |

---

### Layer 3 — Command Submission Path

User-space submits GPU work via `DRM_IOCTL_AMDGPU_CS` (`amdgpu_cs_ioctl`):

```
amdgpu_cs_ioctl()                              amdgpu/amdgpu_cs.c
  │
  ├─ amdgpu_cs_parser_init()
  │    └─ parse chunk list (IB, BO_LIST, DEPENDENCIES, SYNCOBJ_IN/OUT)
  │
  ├─ amdgpu_cs_p1_ib()           — validate IB chunks per ring type
  ├─ amdgpu_cs_p1_bo_handles()   — resolve GEM handles → amdgpu_bo
  │
  ├─ amdgpu_cs_p2_fence()        — allocate hardware fence
  │
  ├─ amdgpu_job_alloc()          — create drm_sched_job wrapper
  │    └─ struct amdgpu_job {
  │         struct drm_sched_job base;
  │         struct amdgpu_vm     *vm;       // per-process VA space
  │         struct amdgpu_ib     ibs[];     // indirect buffer list
  │         uint32_t              vmid;     // PASID/VMID for this job
  │         struct dma_fence     *hw_fence; // GPU completion fence
  │       }
  │
  ├─ amdgpu_cs_emit_fence()      — write FENCE packet into ring
  │
  └─ drm_sched_entity_push_job() — submit to DRM GPU scheduler
       └─ drm_gpu_scheduler runs job:
            └─ amdgpu_job_run()
                 └─ amdgpu_ring_emit_ib()  — write IB exec packet to ring
                      └─ GPU reads ring → fetches & executes IB commands
                           └─ FENCE interrupt → dma_fence_signal()
```

---

### Layer 4 — Virtual Memory (amdgpu_vm)

Each GPU context has a per-process GPU virtual address space:

```
amdgpu_vm
  ├─ vm_pd_addr        — GPU page directory root (2-level or 3-level)
  ├─ vas              — address space (drm_mm allocator)
  ├─ vmid             — hardware VMID (context tag in TLB)
  └─ page_tables[]    — radix tree of PTEs

Page table walk (hardware):
  GPU VA → L2 PDE → L1 PDE → PTE → Physical Address (VRAM or GTT)
```

- **VRAM:** on-card GDDR/HBM (local to GPU, fastest)
- **GTT:** system RAM mapped through GART aperture (CPU↔GPU shared)
- **GART:** 256 MiB–1 GiB aperture, managed via `amdgpu_gart.c`
- VM flush triggered by `amdgpu_vm_flush()` → writes `VM_CONTEXT_PAGE_TABLE_BASE_ADDR` register

---

### Layer 5 — Memory Management (TTM + GEM)

```
amdgpu GEM ioctl (DRM_IOCTL_AMDGPU_GEM_CREATE)
  │
  └─ amdgpu_gem_object_create()
       └─ amdgpu_bo_create()          — struct amdgpu_bo wraps ttm_buffer_object
            └─ ttm_bo_init()          — register with TTM memory manager
                 │
                 ├─ AMDGPU_GEM_DOMAIN_VRAM  → TTM_PL_VRAM  (on-card)
                 ├─ AMDGPU_GEM_DOMAIN_GTT   → TTM_PL_TT    (system RAM / GART)
                 └─ AMDGPU_GEM_DOMAIN_CPU   → TTM_PL_SYSTEM (unbound)

Eviction (VRAM pressure):
  ttm_bo_evict()
    └─ amdgpu_bo_move()
         └─ amdgpu_move_blit()   — SDMA blit: VRAM → GTT
              └─ amdgpu_copy_buffer() → submit SDMA copy job
```

---

### Layer 6 — amdkfd (Kernel Fusion Driver)

KFD provides the `/dev/kfd` char device for compute runtimes (ROCm/HIP/OpenCL):

```
/dev/kfd  ←  hsa-runtime userspace
   │
   └─ kfd_ioctl()                     amdkfd/kfd_chardev.c
        │
        ├─ AMDKFD_IOC_CREATE_QUEUE
        │    └─ kfd_ioctl_create_queue()
        │         └─ pqm_create_queue()   — per-process queue manager
        │              └─ dqm_create_queue() — device queue manager
        │                   └─ program HQD (Hardware Queue Descriptor)
        │
        ├─ AMDKFD_IOC_ALLOC_MEMORY_OF_GPU
        │    └─ kfd_ioctl_alloc_memory_of_gpu()
        │         └─ amdgpu_amdkfd_gpuvm_alloc_memory_of_gpu()
        │              └─ amdgpu_bo_create() → map into GPU VM
        │
        └─ AMDKFD_IOC_MAP_MEMORY_TO_GPU
             └─ amdgpu_amdkfd_gpuvm_map_memory_to_gpu()
                  └─ amdgpu_vm_bo_map() → insert PTEs

KFD ↔ amdgpu bridge:  amdgpu/amdgpu_amdkfd.c
  kgd2kfd_probe()   — called from amdgpu_pci_probe
  kgd2kfd_device_init() — share amdgpu_device with KFD
```

CWSR (Context Save/Restore) for preemptible compute:
- Trap handler firmware loaded per GFX generation (`cwsr_trap_handler_gfxN.asm`)
- Saves all shader register state on preemption, restores on resume

---

### Layer 7 — Display (DC / DAL)

The Display Core (`display/dc/`) is a hardware-agnostic display stack:

```
DRM atomic commit
  │
  └─ amdgpu_dm_atomic_commit_tail()          amdgpu/amdgpu_dm/amdgpu_dm.c
       │
       ├─ dc_commit_state()                  display/dc/core/dc.c
       │    └─ dc_stream_update()
       │         ├─ resource_build_scaling_params()
       │         ├─ dce_transform_set_scaler()
       │         └─ hubp_program_surface_config()
       │
       ├─ dm_enable_per_frame_crtc_master_sync()
       └─ drm_crtc_vblank_on() → drm_handle_vblank()

Display hardware layers:
  DMUB (Display Microcontroller Unit) — firmware for DisplayPort aux, PSR
  DCN (Display Core Next)            — pixel pipeline, color management
  DIO / PHY                          — DisplayPort / HDMI signal output
```

---

### Layer 8 — Power Management

```
pm/swsmu/                          Simplified SMU interface (RDNA+)
  amdgpu_smu.c
  └─ smu_init()
       ├─ smu_set_default_dpm_table()   — frequency tables
       └─ smu_enable_thermal_alert()    — thermal throttling

pm/powerplay/                      Legacy DPM (Polaris/Vega)
pm/legacy-dpm/                     Pre-SI/CIK legacy

Runtime PM:
  amdgpu_device_runtime_suspend()
    ├─ amdgpu_device_ip_suspend()    — suspend all IP blocks
    └─ pci_save_state()

DPM (Dynamic Power Management):
  GPU clock → [min_clk ↔ max_clk] controlled by SMU based on
  thermal sensors, GPU load (busy%), VDDGFX voltage
```

---

## 4. Data Flow Diagrams

### 4a. GPU Command Submission (full path)

```
 Mesa/Vulkan (userspace)       amdgpu kernel         GPU HW
      │                              │                   │
      │  DRM_IOCTL_AMDGPU_CS         │                   │
      ├─────────────────────────────►│                   │
      │                              │ parse IB chunks   │
      │                              │ resolve BO handles│
      │                              │ alloc amdgpu_job  │
      │                              │ alloc HW fence    │
      │                              │ push to drm_sched │
      │                              │                   │
      │                              │ job_run():        │
      │                              │ emit_vm_flush()   │
      │                              │ emit_ib() → ring  │
      │                              ├──────────────────►│
      │                              │   ring wptr bump  │
      │                              │                   │ execute IB
      │                              │                   │ FENCE packet
      │                              │◄──────────────────┤  IRQ
      │                              │ dma_fence_signal()│
      │◄── sync_file / timeline ─────┤                   │
```

### 4b. Memory Object Lifecycle

```
GEM_CREATE ioctl
  └─ amdgpu_bo_create()   → TTM BO in VRAM or GTT
       │
       ├─ GEM_VA ioctl → amdgpu_vm_bo_map()  → PTE inserted in GPU page table
       │
       ├─ CPU access → mmap → fault → amdgpu_bo_fault()
       │                               └─ ttm_bo_vm_fault() → map GTT pages
       │
       └─ VRAM eviction (pressure)
            └─ amdgpu_move_blit()  → SDMA copy VRAM→GTT
                 └─ update PTEs to new GTT location
```

### 4c. Interrupt Handling

```
GPU raises IRQ
  │
  └─ amdgpu_irq_handler()             amdgpu/amdgpu_irq.c
       └─ amdgpu_ih_process()         (IH ring reader)
            └─ foreach entry in IH ring:
                 ├─ amdgpu_irq_dispatch() → find irq_src by client_id/src_id
                 ├─ GFX fence IRQ   → amdgpu_fence_process() → dma_fence_signal()
                 ├─ Display IRQ     → drm_handle_vblank()
                 ├─ SDMA IRQ        → fence signal
                 └─ RAS/ECC error   → amdgpu_ras_interrupt_handler()
```

### 4d. ASIC Generation to IP Block Mapping

```
ASIC Gen        GFX IP    SDMA    Display    Video
────────────────────────────────────────────────────────
SI  (7xxx)      gfx_v6_0  sdma_v2 dce_v6    uvd_v3/vce_v2
CIK (8xxx)      gfx_v7_0  sdma_v2 dce_v8    uvd_v4/vce_v2
VI  (Polaris)   gfx_v8_0  sdma_v3 dce_v11   uvd_v6/vce_v3
Vega (gfx9)     gfx_v9_0  sdma_v4 dcn_v1    vcn_v1_0
Navi (gfx10)    gfx_v10_1 sdma_v5 dcn_v2    vcn_v2_0
RDNA2 (gfx10.3) gfx_v10_3 sdma_v5 dcn_v3    vcn_v3_0
RDNA3 (gfx11)   gfx_v11_0 sdma_v6 dcn_v3.2  vcn_v4_0
RDNA4 (gfx12)   gfx_v12_0 sdma_v7 dcn_v4    vcn_v5_0
```

---

## 5. Key Source Files Quick Reference

### amdgpu/ (core driver)

| File | Purpose |
|---|---|
| `amdgpu_drv.c` | PCI driver, module init, `amdgpu_kms_pci_driver` |
| `amdgpu_device.c` | `amdgpu_device_init()`, IP block orchestration |
| `amdgpu.h` | `amdgpu_device`, `amdgpu_ring`, `amdgpu_ip_block` structs |
| `amdgpu_cs.c` | `amdgpu_cs_ioctl()` — command submission parser |
| `amdgpu_ring.c` | Ring buffer alloc, wptr write, emit helpers |
| `amdgpu_ib.c` | Indirect buffer alloc/free |
| `amdgpu_job.c` | `amdgpu_job_alloc()`, `amdgpu_job_run()`, scheduler integration |
| `amdgpu_fence.c` | GPU fence driver, `amdgpu_fence_process()` |
| `amdgpu_vm.c` | GPU VA space, `amdgpu_vm_bo_map()`, page table updates |
| `amdgpu_gmc.c` | Memory controller init, GART setup |
| `amdgpu_ttm.c` | TTM integration, VRAM/GTT placement, `amdgpu_move_blit()` |
| `amdgpu_gem.c` | GEM object create/free, mmap |
| `amdgpu_irq.c` | IRQ setup, IH dispatch table |
| `amdgpu_ih.c` | Interrupt Handler ring reader |
| `amdgpu_gfx.c` | GFX engine init, ring allocation |
| `amdgpu_sdma.c` | SDMA engine init, copy queue |
| `amdgpu_vcn.c` | Video Codec Next unified init |
| `amdgpu_ctx.c` | Context (`DRM_AMDGPU_CTX`) lifecycle |
| `amdgpu_amdkfd.c` | KFD↔amdgpu bridge |
| `amdgpu_pm.c` | Sysfs PM interface (clocks, sensors) |

### amdkfd/ (compute runtime)

| File | Purpose |
|---|---|
| `kfd_chardev.c` | `/dev/kfd` ioctl handler |
| `kfd_device.c` | KFD device init, kgd2kfd interface |
| `kfd_process.c` | Per-process state, PASID management |
| `kfd_queue.c` | Queue create/destroy abstraction |
| `kfd_device_queue_manager.c` | HQD programming per ASIC gen |
| `kfd_priv.h` | All major KFD structs (`kfd_dev`, `kfd_process`, `kfd_queue`) |

### display/ (display core)

| File | Purpose |
|---|---|
| `amdgpu_dm/amdgpu_dm.c` | DRM ↔ DC bridge, `dm_atomic_commit_tail()` |
| `dc/core/dc.c` | Display Core top-level, `dc_commit_state()` |
| `dc/dc.h` | Public DC API |
| `dmub/` | DMUB firmware service (AUX, PSR, ABM) |
| `modules/color/` | Color management pipeline |

---

## 6. IOCTL Surface

| IOCTL | Command | Purpose |
|---|---|---|
| `DRM_IOCTL_AMDGPU_GEM_CREATE` | 0x00 | Allocate GPU buffer object |
| `DRM_IOCTL_AMDGPU_GEM_MMAP` | 0x01 | Map BO offset for CPU mmap |
| `DRM_IOCTL_AMDGPU_CTX` | 0x02 | Alloc/free GPU context (VM + timeline) |
| `DRM_IOCTL_AMDGPU_BO_LIST` | 0x03 | Create buffer object list for CS |
| `DRM_IOCTL_AMDGPU_CS` | 0x04 | Submit command stream |
| `DRM_IOCTL_AMDGPU_INFO` | 0x05 | Query HW info, VRAM usage, timestamps |
| `DRM_IOCTL_AMDGPU_GEM_VA` | 0x08 | Map/unmap BO into GPU VA space |
| `DRM_IOCTL_AMDGPU_WAIT_CS` | 0x09 | Wait for CS completion fence |
| `DRM_IOCTL_AMDGPU_GEM_USERPTR` | 0x11 | Register CPU userptr as GPU BO |
| `DRM_IOCTL_AMDGPU_WAIT_FENCES` | 0x12 | Wait for multiple fences |
| `DRM_IOCTL_AMDGPU_VM` | 0x13 | Reserve/unreserve VMID |
| `DRM_IOCTL_AMDGPU_FENCE_TO_HANDLE` | 0x14 | Export fence as sync_file |

---

## 7. Power Management Summary

```
Idle detection:
  amdgpu_dpm_idle_power_profile_enter()
    └─ SMU: set min GFX/MEM clocks, enable clock gating

Active workload:
  amdgpu_dpm_set_power_profile_mode()
    └─ SMU: raise clocks based on GPU_BUSY_PERCENT sensor

Thermal throttle:
  SMU autonomous P-state changes  (independent of driver)
  amdgpu_thermal_interrupt_handler()
    └─ emit DRM_EVENT if threshold crossed

Runtime suspend:
  amdgpu_device_runtime_suspend()
    ├─ amdgpu_device_ip_suspend()   — IP block power-off sequence
    ├─ PSP: save firmware context
    └─ pci_set_power_state(D3hot)
```

---

## 8. ASIC Support Range

Driver version: **3.64.0** (KMS API version)

- **SI** (GFX6): Tahiti, Pitcairn, Verde, Oland, Hainan
- **CIK** (GFX7): Kaveri, Bonaire, Hawaii, Kabini, Mullins
- **VI** (GFX8): Polaris10/11/12, Topaz, Tonga, Fiji, Carrizo, Stoney
- **Vega** (GFX9): Vega10, Vega12, Vega20, Arcturus (MI100), Aldebaran (MI200)
- **Navi** (GFX10): Navi10, Navi12, Navi14, Sienna Cichlid, Navy Flounder
- **RDNA2** (GFX10.3): Yellow Carp, Beige Goby, Dimgrey Cavefish, Vangogh
- **RDNA3** (GFX11): Navi31/32/33 (RX 7xxx), Phoenix APU
- **RDNA4** (GFX12): Navi48/44 (RX 9xxx) — IP Discovery path

---

## References

- `drivers/gpu/drm/amd/amdgpu/amdgpu_drv.c` — PCI driver, module entry
- `drivers/gpu/drm/amd/amdgpu/amdgpu_device.c` — `amdgpu_device_init`
- `drivers/gpu/drm/amd/amdgpu/amdgpu_cs.c` — command submission
- `drivers/gpu/drm/amd/amdgpu/amdgpu_vm.c` — GPU virtual memory
- `drivers/gpu/drm/amd/amdkfd/kfd_chardev.c` — KFD ioctl dispatch
- `drivers/gpu/drm/amd/display/amdgpu_dm/amdgpu_dm.c` — DRM/DC bridge
- `include/uapi/drm/amdgpu_drm.h` — UAPI ioctl definitions
- `drivers/gpu/drm/amd/include/amd_shared.h` — IP block type enum
