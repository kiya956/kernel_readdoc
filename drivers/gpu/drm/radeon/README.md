# DRM Radeon Driver — Deep Dive Analysis

> **Source tree:** `drivers/gpu/drm/radeon/`
> **Kernel:** noble-linux-oem
> **Date:** 2026-04-28
> **Scanned from:** ~/canonical/kernel/noble-linux-oem

---

## 1. Full Subsystem Stack

```
╔══════════════════════════════════════════════════════════════════════╗
║                       USER SPACE                                     ║
║  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌───────────────────┐   ║
║  │ Mesa r600│  │ RadeonSI │  │ VAAPI    │  │ OpenCL (clover)   │   ║
║  │ (GL ≤R700)│ │ (GL SI+) │  │ (UVD)    │  │                   │   ║
║  └────┬─────┘  └────┬─────┘  └────┬─────┘  └───────┬───────────┘   ║
║       └─────────────┴──────┬───────┴─────────────────┘              ║
║                            │  libdrm_radeon (CS / GEM ioctls)        ║
╚════════════════════════════╪════════════════════════════════════════╝
                             │  ioctl: GEM_CREATE / CS / INFO / WAIT
╔════════════════════════════╪════════════════════════════════════════╗
║  KERNEL — radeon.ko                                                 ║
║  ┌─────────────────────────▼────────────────────────────────────┐   ║
║  │  PCI driver: radeon_kms_pci_driver (radeon_drv.c:596)         │   ║
║  │  probe → radeon_pci_probe (radeon_drv.c:259)                  │   ║
║  │  load  → radeon_driver_load_kms → radeon_device_init          │   ║
║  └────────────────────────────┬─────────────────────────────────┘   ║
║                               │                                     ║
║  ┌────────────────────────────▼─────────────────────────────────┐   ║
║  │  radeon_device (radeon.h:2296)                                │   ║
║  │  ┌───────────────────────────────────────────────────────┐   │   ║
║  │  │ ddev (drm_device)  │ pdev (pci_dev)                   │   │   ║
║  │  │ family             │ flags                             │   │   ║
║  │  │ asic (*radeon_asic) → per-generation ops table         │   │   ║
║  │  │ mc (radeon_mc)     │ gart (radeon_gart)                │   │   ║
║  │  │ ring[8] (radeon_ring) → GFX/CP1/CP2/DMA0/DMA1/UVD/VCE│   │   ║
║  │  │ fence_drv[8]       │ irq (radeon_irq)                 │   │   ║
║  │  │ mman (radeon_mman → TTM)  │ pm (radeon_pm)            │   │   ║
║  │  │ uvd / vce          │ gem (radeon_gem)                  │   │   ║
║  │  └───────────────────────────────────────────────────────┘   │   ║
║  └──────────────────────────────────────────────────────────────┘   ║
║                                                                      ║
║  ┌── ASIC layer (per-generation) ──────────────────────────────┐    ║
║  │ radeon_asic (radeon.h:1831)                                  │    ║
║  │  init/fini/resume/suspend/asic_reset                         │    ║
║  │  gart: tlb_flush / get_page_entry / set_page                 │    ║
║  │  ring[i]: ib_execute / emit_fence / emit_semaphore           │    ║
║  │  irq: set / process                                          │    ║
║  │  display: bandwidth_update / hpd / page_flip                 │    ║
║  │  pm: get_dynpm_state / get_engine_clock / set_*              │    ║
║  │                                                               │    ║
║  │  Implementations:                                             │    ║
║  │    r100.c, r300.c, r600.c, rv770.c, evergreen.c,            │    ║
║  │    ni.c, si.c (GCN 1.0), cik.c (GCN 1.1)                   │    ║
║  └──────────────────────────────────────────────────────────────┘    ║
║                                                                      ║
║  ┌── Buffer management (TTM) ──────────────────────────────────┐    ║
║  │  radeon_bo (radeon.h:482)                                     │    ║
║  │  ┌────────────────────────────────────────────────────────┐  │    ║
║  │  │ tbo (ttm_buffer_object)  │ initial_domain (u32)        │  │    ║
║  │  │ placements[4]            │ placement (ttm_placement)   │  │    ║
║  │  │ kmap (ttm_bo_kmap_obj)   │ kptr (void*)               │  │    ║
║  │  │ tiling_flags / pitch     │ surface_reg                 │  │    ║
║  │  └────────────────────────────────────────────────────────┘  │    ║
║  └──────────────────────────────────────────────────────────────┘    ║
║                                                                      ║
║  ┌── Command submission ───────────────────────────────────────┐    ║
║  │  radeon_cs_ioctl (radeon_cs.c:669)                            │    ║
║  │    → radeon_cs_parser_init (L265) → parse user chunks         │    ║
║  │    → validate BOs → build IB → radeon_ib_schedule             │    ║
║  │    → radeon_fence_emit (radeon_fence.c:133)                   │    ║
║  └──────────────────────────────────────────────────────────────┘    ║
║                                                                      ║
║  ┌── Ring / fence infrastructure ──────────────────────────────┐    ║
║  │  radeon_ring (radeon.h:790) — 8 rings max                    │    ║
║  │  GFX(0) CP1(1) CP2(2) DMA0(3) DMA1(4) UVD(5) VCE(6,7)     │    ║
║  │                                                               │    ║
║  │  radeon_fence (radeon.h:374) — dma_fence subclass             │    ║
║  │    emit → process → signaled → wait                           │    ║
║  └──────────────────────────────────────────────────────────────┘    ║
╚════════════════════════════╪════════════════════════════════════════╝
                             │  PCIe BAR / MMIO
╔════════════════════════════╪════════════════════════════════════════╗
║  HARDWARE                  ▼                                        ║
║  R100─R300─R500 (fixed)  R600─RV770─Evergreen─NI (VLIW)            ║
║  SI (GCN 1.0)  CIK (GCN 1.1)                                       ║
║  [ VRAM ]  [ GART/GTT ]  [ Ring buffers ]  [ IRQ/MSI ]             ║
╚════════════════════════════════════════════════════════════════════╝
```

---

## 2. Component Details

### ASIC Generations

The driver supports all AMD/ATI GPUs from R100 (Radeon 7200) to CIK (R7/R9 200):

| File | GPU Generation | Architecture |
|---|---|---|
| `r100.c` | R100-R200 | Fixed pipeline, AGP GART |
| `r300.c` | R300-R500 | Programmable vertex/pixel shaders |
| `r600.c` | R600-RV770 | Unified shaders (VLIW5) |
| `evergreen.c` | Evergreen (HD5000) | VLIW5 + new interrupt system |
| `ni.c` | Northern Islands (HD6000) | VLIW4 |
| `si.c` | Southern Islands (HD7000) | GCN 1.0 |
| `cik.c` | Sea Islands (R7/R9 200) | GCN 1.1 + compute queues |

### radeon_device (radeon.h:2296)

```c
struct radeon_device {
    struct device        *dev;
    struct drm_device     ddev;            // embedded DRM device
    struct pci_dev       *pdev;
    enum radeon_family    family;           // GPU generation
    unsigned long         flags;
    struct radeon_asic   *asic;            // per-gen ops table
    struct radeon_mc      mc;              // memory controller config
    struct radeon_gart    gart;            // GART table management
    struct radeon_mman    mman;            // TTM memory manager
    struct radeon_ring    ring[RADEON_NUM_RINGS]; // 8 rings
    struct radeon_fence_driver fence_drv[RADEON_NUM_RINGS];
    struct radeon_irq     irq;
    struct radeon_pm      pm;              // power management
    struct radeon_uvd     uvd;             // video decode
    struct radeon_vce     vce;             // video encode
    ...
};
```

### radeon_bo (radeon.h:482) — Buffer Object

```c
struct radeon_bo {
    struct list_head        list;
    u32                     initial_domain;   // VRAM / GTT / SYSTEM
    struct ttm_place        placements[4];
    struct ttm_placement    placement;
    struct ttm_buffer_object tbo;             // TTM base object
    struct ttm_bo_kmap_obj  kmap;
    u32                     flags;
    void                   *kptr;             // kernel mapping pointer
    u32                     tiling_flags;
    u32                     pitch;
    int                     surface_reg;
    unsigned                prime_shared_count;
};
```

### Rings (radeon.h:139-158)

```
RADEON_RING_TYPE_GFX   (0)  — main 3D rendering ring
CAYMAN_RING_TYPE_CP1   (1)  — compute ring 1 (Cayman+)
CAYMAN_RING_TYPE_CP2   (2)  — compute ring 2 (Cayman+)
R600_RING_TYPE_DMA     (3)  — DMA copy engine
CAYMAN_RING_TYPE_DMA1  (4)  — second DMA engine
R600_RING_TYPE_UVD     (5)  — UVD video decode
TN_RING_TYPE_VCE1      (6)  — VCE video encode ring 1
TN_RING_TYPE_VCE2      (7)  — VCE video encode ring 2
RADEON_NUM_RINGS = 8
```

### radeon_asic (radeon.h:1831)

```c
struct radeon_asic {
    int (*init)(struct radeon_device *rdev);
    void (*fini)(struct radeon_device *rdev);
    int (*resume)(struct radeon_device *rdev);
    int (*suspend)(struct radeon_device *rdev);
    int (*asic_reset)(struct radeon_device *rdev, bool hard);
    void (*mmio_hdp_flush)(struct radeon_device *rdev);
    bool (*gui_idle)(struct radeon_device *rdev);
    struct { void (*tlb_flush)(...); ... } gart;
    struct { int (*init)(...); void (*copy_pages)(...); ... } vm;   // VM page table ops
    struct { ... } ring;  // per-ring ops
    struct { ... } irq;
    struct { ... } display;
    struct { ... } pm;    // power management
};
```

---

## 3. Workflow: Command Submission

**Source:** `radeon_cs.c:669` → `radeon_fence.c:133`

```
 Userspace (Mesa)                  Kernel (radeon.ko)                 GPU HW
      │                                   │                               │
      │  DRM_IOCTL_RADEON_CS              │                               │
      ├──────────────────────────────────►│                               │
      │                                   │  radeon_cs_ioctl (L669)       │
      │                                   │                               │
      │                                   │  1. radeon_cs_parser_init     │
      │                                   │     parse chunks from user    │
      │                                   │                               │
      │                                   │  2. validate BOs (TTM)        │
      │                                   │     → ttm_bo_validate each BO │
      │                                   │     → pin in VRAM/GTT         │
      │                                   │                               │
      │                                   │  3. Build IB (indirect buf)   │
      │                                   │     copy cmds → ring buffer   │
      │                                   │                               │
      │                                   │  4. radeon_ib_schedule        │
      │                                   │     → asic->ring.ib_execute() │
      │                                   │     → radeon_fence_emit()     │
      │                                   ├──────────────────────────────►│
      │                                   │  5. GPU executes, writes seq  │
      │                                   │◄──────────────────────────────┤
      │                                   │     IRQ → fence_process()     │
      │◄── fence_wait returns ────────────┤                               │
```

---

## 4. Key Source Files

| File | Purpose |
|---|---|
| `radeon_drv.c` | PCI driver registration, module init, kms_driver ops |
| `radeon_device.c` | Device init (L1278)/fini (L1511), suspend (L1544), resume (L1650), GPU reset (L1755) |
| `radeon.h` | All major data structures (~2900 lines) |
| `radeon_cs.c` | Command submission ioctl (L669) |
| `radeon_fence.c` | Fence emit (L133)/process (L319)/wait (L560) |
| `radeon_ring.c` | Ring buffer management |
| `radeon_gem.c` | GEM object creation (L93), ioctl handlers |
| `radeon_object.c` | BO create/pin/unpin (TTM wrapper) |
| `radeon_display.c` | KMS display (CRTC, connector, encoder) |
| `radeon_pm.c` | Power management, clock/voltage |
| `radeon_irq_kms.c` | IRQ handler registration |
| `cik.c` | Sea Islands (GCN 1.1) ASIC implementation (~10K lines) |
| `si.c` | Southern Islands (GCN 1.0) ASIC implementation (~7K lines) |

---

## References

- `radeon.h:2296` — `struct radeon_device`
- `radeon.h:482` — `struct radeon_bo`
- `radeon.h:1831` — `struct radeon_asic`
- `radeon.h:790` — `struct radeon_ring`
- `radeon.h:374` — `struct radeon_fence`
- `radeon_cs.c:669` — `radeon_cs_ioctl`
- `radeon_device.c:1278` — `radeon_device_init`
- `radeon_fence.c:133` — `radeon_fence_emit`
