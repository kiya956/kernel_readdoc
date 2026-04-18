# Nouveau Driver — Deep Dive Analysis

> **Source tree:** `drivers/gpu/drm/nouveau/`
> **Kernel:** noble-linux-oem (oem-6.17-next)
> **Date:** 2026-04-18

---

## 1. Full Subsystem Stack

```
╔══════════════════════════════════════════════════════════════════════════════╗
║                           USER SPACE                                         ║
║  ┌────────────────┐  ┌────────────────┐  ┌──────────────┐  ┌─────────────┐  ║
║  │  Mesa/NVK      │  │  Mesa/NVC0     │  │  VDPAU /     │  │  Wayland /  ║
║  │  (Vulkan)      │  │  (OpenGL)      │  │  VA-API      │  │  X11        ║
║  └───────┬────────┘  └───────┬────────┘  └──────┬───────┘  └──────┬──────┘  ║
║          └──────────────────┴───────────────────┴─────────────────┘         ║
║                          │ libdrm_nouveau  (pushbuf / GEM ioctls)           ║
╚══════════════════════════╪═════════════════════════════════════════════════╝
                            │  ioctl()
╔══════════════════════════╪═════════════════════════════════════════════════╗
║  DRM CORE                 ▼                                                 ║
║  drm_ioctl() ──► drm_ioctls[] ──► nouveau ioctl table                      ║
╚══════════════════════════╪═════════════════════════════════════════════════╝
                            │
╔══════════════════════════╪═════════════════════════════════════════════════╗
║  NOUVEAU DRM LAYER        ▼                                                 ║
║  (nouveau_drm.c / nouveau_*.c)                                              ║
║                                                                              ║
║  ┌─────────────────────────────────────────────────────────────────────┐    ║
║  │                    struct nouveau_drm (root)                        │    ║
║  │  ┌──────────────┐  ┌────────────────┐  ┌─────────┐  ┌──────────┐  │    ║
║  │  │  TTM memory  │  │  nouveau_chan[] │  │  fence  │  │  display │  │    ║
║  │  │  (VRAM/GART) │  │  push buffer   │  │  context│  │  KMS/drm │  │    ║
║  │  └──────────────┘  └────────────────┘  └─────────┘  └──────────┘  │    ║
║  └─────────────────────────────────────────────────────────────────────┘    ║
║          │  nvif_*()  (NVIF RPC interface)                                  ║
╠══════════╪═════════════════════════════════════════════════════════════════╣
║  NVIF LAYER (nvif/)     ▼                                                   ║
║  Userspace ↔ kernel RPC / object handles                                   ║
╠══════════╪═════════════════════════════════════════════════════════════════╣
║  NVKM HARDWARE ABSTRACTION LAYER  (nvkm/)   ▼                               ║
║                                                                              ║
║  ┌─────────────────────────────────────────────────────────────────────┐    ║
║  │                    nvkm_device  (per-GPU)                           │    ║
║  │                                                                     │    ║
║  │  Subdevices (nvkm_subdev):                                          │    ║
║  │  ┌───────┐ ┌───────┐ ┌───────┐ ┌───────┐ ┌───────┐ ┌───────────┐  │    ║
║  │  │  MC   │ │  FB   │ │  MMU  │ │ BIOS  │ │  CLK  │ │  BUS/GPIO │  │    ║
║  │  └───────┘ └───────┘ └───────┘ └───────┘ └───────┘ └───────────┘  │    ║
║  │                                                                     │    ║
║  │  Engines (nvkm_engine extends nvkm_subdev):                         │    ║
║  │  ┌───────┐ ┌───────┐ ┌───────┐ ┌───────┐ ┌───────┐ ┌───────────┐  │    ║
║  │  │  GR   │ │ FIFO  │ │  CE   │ │ DISP  │ │  SEC2 │ │   NVDEC   │  │    ║
║  │  │(3D/CU)│ │(sched)│ │(DMA)  │ │(disp) │ │(sec.) │ │  (video)  │  │    ║
║  │  └───────┘ └───────┘ └───────┘ └───────┘ └───────┘ └───────────┘  │    ║
║  └─────────────────────────────────────────────────────────────────────┘    ║
╚══════════════════════════════════════════════════════════════════════════════╝
               │  PCIe MMIO (BAR0) / DMA / IRQ
╔══════════════════════════════════════════════════════════════════════════════╗
║  NVIDIA GPU HARDWARE                                                          ║
║  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌────────────────┐  ║
║  │  GPC / SMs   │  │  Copy Engine │  │   Video Dec  │  │  Display (DCE) ║
║  │  (3D/compute)│  │  (async DMA) │  │   (NVDEC)    │  │  Pipes/Heads   │  ║
║  └──────────────┘  └──────────────┘  └──────────────┘  └────────────────┘  ║
║  ┌──────────────┐  ┌──────────────┐  ┌────────────────────────────────────┐ ║
║  │  FIFO / TSG  │  │  MMU / PD    │  │  VRAM (GDDR/HBM) + GART aperture  │ ║
║  │  (channel HW)│  │  (page table)│  │                                    │ ║
║  └──────────────┘  └──────────────┘  └────────────────────────────────────┘ ║
╚══════════════════════════════════════════════════════════════════════════════╝
```

---

## 2. Directory Map

| Directory | Purpose |
|---|---|
| `nouveau/` (root) | DRM integration: TTM, GEM, channels, fences, display KMS |
| `nvkm/core/` | Object model, device, subdev, engine, memory primitives |
| `nvkm/engine/` | Per-engine impls: `gr/`, `fifo/`, `disp/`, `ce/`, `sec2/`, `nvdec/` |
| `nvkm/subdev/` | Per-subdev impls: `mmu/`, `fb/`, `mc/`, `clk/`, `bios/`, `gsp/` |
| `nvkm/falcon/` | Falcon microcontroller framework (firmware loaders) |
| `nvkm/nvfw/` | Firmware binary format helpers |
| `nvif/` | NVIF interface layer — kernel↔user object RPC |
| `dispnv50/` | NV50+ atomic KMS (modern display path) |
| `dispnv04/` | NV04–NV40 legacy modesetting |
| `include/` | nvkm/nvif shared headers, register definitions |

---

## 3. Layer-by-Layer Component Explanation

### Layer 0 — Hardware

NVIDIA GPU hardware is organized around independent engines, each consuming
work from **channels** (command streams). The FIFO engine schedules channels
onto hardware engines based on runlist priority.

| Hardware Block | Role |
|---|---|
| GPC / SM (Streaming Multiprocessors) | 3D vertex/fragment shading + compute |
| FIFO / TSG (Time-Slice Group) | Channel scheduling onto engines |
| Copy Engine (CE/COPY) | Async memory copy between VRAM ↔ GART ↔ system RAM |
| Display Engine (DCE/DCN) | Scanout, compositor, DP/HDMI/eDP |
| NVDEC / NVENC | Hardware video decode / encode |
| MMU | Per-context GPU page tables (2-level on NV50, multi-level on GF100+) |
| PMU / GSP | Power management unit / Graphics System Processor (Turing+) |

---

### Layer 1 — NVKM Object Model

NVKM provides a tree of typed objects with a consistent lifecycle:

```
nvkm_object  (base)
  ├─ nvkm_subdev     (device peripheral: MC, FB, MMU, BUS, CLK, BIOS…)
  │    └─ nvkm_engine  (execution unit: GR, FIFO, CE, DISP, SEC2…)
  └─ nvkm_client     (per-process object namespace)
```

Every object exports `nvkm_object_func`:

```c
struct nvkm_object_func {
    void *(*dtor)(struct nvkm_object *);
    int  (*init)(struct nvkm_object *);
    int  (*fini)(struct nvkm_object *, bool suspend);
    int  (*mthd)(struct nvkm_object *, u32 mthd, void *data, u32 size);
    int  (*map) (struct nvkm_object *, ...);
    int  (*bind)(struct nvkm_object *, struct nvkm_gpuobj *, int align,
                 struct nvkm_gpuobj **);
    int  (*sclass)(struct nvkm_object *, int index, struct nvkm_oclass *);
};
```

**nvkm_device** is the root; subdevices are embedded or allocated via
per-chip constructor tables defined in `nvkm/engine/device/base.c`:

```c
static const struct nvkm_device_chip gf100_chipset = {
    .name  = "GF100",
    .fb    = { 0x1, gf100_fb_new    },
    .fifo  = { 0x1, gf100_fifo_new  },
    .gr    = { 0x1, gf100_gr_new    },
    .mmu   = { 0x1, gf100_mmu_new   },
    .mc    = { 0x1, gf100_mc_new    },
    .disp  = { 0x1, gf119_disp_new  },
    .ce    = { 0x3, gf100_ce_new    },
    // …
};
```

---

### Layer 2 — NVKM FIFO (Channel Scheduling)

The FIFO engine owns GPU command-stream scheduling:

```
nvkm_fifo  (engine)
  ├─ nvkm_runl[]   (runlist — one per engine class / TSG group)
  │    ├─ nvkm_runq   (runqueue — hardware scheduling unit)
  │    └─ nvkm_cgrp   (channel group / TSG)
  │         └─ nvkm_chan  (individual GPU channel)
  └─ nvkm_chid     (channel ID allocator, per runlist)
```

**struct nvkm_chan** key fields:

```c
struct nvkm_chan {
    struct nvkm_cgrp  *cgrp;      // parent TSG
    u16                id;         // hardware channel ID
    struct nvkm_memory *inst;      // instance block (GPU RAM)
    struct nvkm_memory *push;      // push buffer storage
    u64                push_addr;  // GPU VA of push buffer
    struct nvkm_runl  *runl;       // owning runlist
    struct nvkm_vmm   *vmm;        // virtual address space
    struct nvkm_cctx **cctx;       // per-engine GPU contexts
};
```

---

### Layer 3 — nouveau_channel (DRM push-buffer wrapper)

`struct nouveau_channel` (in `nouveau/nouveau_chan.c`) sits above `nvkm_chan`
and manages the CPU-side push buffer ring:

```c
struct nouveau_channel {
    struct nvif_chan    chan;        // NVIF handle to nvkm channel
    struct nouveau_cli *cli;        // client that owns this channel
    struct nouveau_vmm *vmm;        // GPU virtual address space

    struct {
        struct nouveau_bo  *buffer; // TTM BO backing the push buffer
        struct nouveau_vma *vma;    // VA mapping of the push buffer
        u64                 addr;   // GPU address
    } push;

    struct {
        int max;   // ring size (DWORDs)
        int free;  // free DWORDs
        int cur;   // current write position
        int put;   // last flushed position
    } dma;

    u32 user_get;                   // GPU-read GET register (doorbell)
    u32 user_put;                   // CPU-write PUT register (doorbell)
};
```

**Push-buffer submission macros** (`nouveau_dma.h`):

```c
RING_SPACE(chan, n)   // reserve n DWORDs
OUT_RING(chan, dw)    // write one DWORD to ring
FIRE_RING(chan)       // advance PUT → GPU sees new work
```

---

### Layer 4 — Memory Management

```
nouveau_bo  (TTM buffer object)
  ├─ domain: NOUVEAU_GEM_DOMAIN_VRAM | GART | CPU
  └─ nvkm_memory (physical allocation)

nouveau_vmm  (per-context GPU VA space)
  └─ nvkm_vmm  (page table root)
       ├─ nv04_vmm  — flat 32-bit aperture
       ├─ nv50_vmm  — 40-bit unified space
       ├─ gf100_vmm — 40-bit, large page support
       └─ gp100_vmm — 49-bit, ATS support

GART: NVIDIA AGP/PCI aperture for CPU↔GPU shared memory
      managed via nvkm/subdev/mmu/

TTM placement:
  VRAM  → TTM_PL_VRAM   (on-card GDDR/HBM, fastest)
  GART  → TTM_PL_TT     (system RAM mapped through GART)
  HOST  → TTM_PL_SYSTEM (CPU-only, unbound)

Eviction path:
  ttm_bo_evict()
    └─ nouveau_bo_move()
         └─ nouveau_bo_move_m2mf()  (COPY engine DMA: VRAM→GART)
              └─ nouveau_channel push: NV50_MEMORY_TO_MEMORY_FORMAT
```

---

### Layer 5 — NVIF Interface Layer

NVIF (`nvif/`) is the kernel↔userspace RPC bridge. Userspace libraries
call `DRM_NOUVEAU_NVIF` ioctl passing object handles and method IDs;
the kernel dispatches to `nvkm_object::mthd()`.

```
libdrm_nouveau (userspace)
   nvif_object_mthd(obj, mthd, data, size)
     └─ DRM_NOUVEAU_NVIF ioctl
          └─ nouveau_abi16_ioctl() / nvkm_ioctl()
               └─ nvkm_object_mthd()  →  per-class method handler
```

Key NVIF objects: `nvif_device`, `nvif_mmu`, `nvif_vmm`, `nvif_mem`, `nvif_chan`.

---

### Layer 6 — Display (dispnv50 atomic path)

For NV50+ hardware, nouveau uses DRM atomic KMS routed through `dispnv50/`:

```
drm_atomic_commit()
  └─ nouveau_display_commit()
       └─ nv50_display_atomic_commit()
            └─ nv50_disp_atomic_commit_tail()
                 ├─ nv50_core_update()   — head (CRTC) programming
                 ├─ nv50_wndw_update()   — window (plane) update
                 ├─ nv50_outp_update()   — output (DP/HDMI) routing
                 └─ FIRE_RING(core_chan) — GPU DMA push to display engine

Display object hierarchy (dispnv50/):
  nv50_disp   (top-level — owns core channel)
    ├─ nv50_head[]   (CRTC — one per scanout head)
    ├─ nv50_wndw[]   (planes — base, overlay, cursor)
    └─ nv50_outp[]   (connectors — DP, HDMI, DAC)
         ├─ nv50_dp   (DP link training, DPCD)
         └─ nv50_hdmi (HDMI audio, infoframes)
```

---

### Layer 7 — GSP (Graphics System Processor, Turing+)

On Turing and newer chips, nouveau can use NVIDIA's GSP firmware to
offload RM (Resource Manager) tasks:

```
nvkm/subdev/gsp/
  ├─ ga102.c, gh100.c, ad102.c, gb100.c   — per-chip GSP init
  └─ Falcon microcontroller:
       nvkm_falcon_load_firmware()
         └─ load GSP-RM binary → execute on GPU Falcon engine
              └─ GSP handles: channel init, clock management,
                              memory allocation, RM method dispatch
```

---

## 4. Data Flow Diagrams

### 4a. GPU Command Submission (push-buffer path)

```
 Mesa (userspace)              nouveau kernel           GPU HW
      │                              │                     │
      │  NOUVEAU_GEM_PUSHBUF ioctl   │                     │
      ├─────────────────────────────►│                     │
      │                              │ validate BOs        │
      │                              │ apply relocations   │
      │                              │ RING_SPACE()        │
      │                              │ OUT_RING(cmds)      │
      │                              │ FIRE_RING()         │
      │                              │  → write PUT reg    │
      │                              ├────────────────────►│
      │                              │   FIFO reads PUT    │
      │                              │                     │ execute
      │                              │                     │ SEMAPHORE
      │                              │◄────────────────────┤ interrupt
      │                              │ nouveau_fence_done()│
      │◄── DRM_FENCE signal ─────────┤                     │
```

### 4b. Modern VM_BIND + EXEC path (Kepler+ / Mesa NVK)

```
 Mesa NVK (Vulkan)             nouveau kernel
      │                              │
      │  NOUVEAU_VM_BIND ioctl       │
      ├─────────────────────────────►│
      │                              │ nouveau_uvmm_ioctl_vm_bind()
      │                              │   → nouveau_uvmm_sm_map()
      │                              │   → nvkm_vmm PTE insert
      │                              │
      │  NOUVEAU_EXEC ioctl          │
      ├─────────────────────────────►│
      │                              │ nouveau_exec_ioctl_exec()
      │                              │   → build push buffer
      │                              │   → drm_sched_entity_push_job()
      │                              │        └─ job_run → FIRE_RING()
      │◄── sync_file fd ─────────────┤
```

### 4c. NVKM Device Initialization

```
nouveau_drm_probe()
  │
  ├─ nvkm_device_pci_new()
  │    ├─ match PCI ID → select nvkm_device_chip descriptor
  │    ├─ map BAR0 (MMIO primary)
  │    └─ instantiate all subdevices (foreach chip->xxx constructor)
  │
  ├─ nouveau_drm_device_new()   → allocate nouveau_drm, init DRM dev
  │
  └─ nouveau_drm_device_init()
       ├─ nvkm_device_init()     → preinit → oneinit → init each subdev
       │    order: MC → BUS → TIMER → GPIO → I2C → FUSE →
       │           MXM → BIOS → CLK → FB → VOLT → ICCSENSE →
       │           THERM → MMU → GSP → PDISP → PMMU →
       │           FIFO → GR → CE → NVDEC → NVENC → DISP
       ├─ nouveau_ttm_init()     → TTM VRAM + GART managers
       ├─ nouveau_display_init() → register DRM KMS
       └─ nouveau_accel_init()   → create copy/accel channels
```

### 4d. GPU Interrupt Handling

```
IRQ fires
  └─ nouveau_drm_irq()              nouveau_drm.c
       └─ nvkm_intr_top()           nvkm/core/intr.c
            └─ nvkm_mc_intr()       nvkm/subdev/mc/
                 ├─ GR stall/nonstall → gr_intr()  → context switch
                 ├─ FIFO fault      → fifo_intr() → channel kill
                 ├─ DISP interrupt  → disp_intr() → drm_handle_vblank()
                 └─ CE interrupt    → ce_intr()   → fence signal
                      └─ nouveau_fence_context_put()
                           └─ dma_fence_signal()
```

---

## 5. Hardware Generation Timeline

| Card Type | Arch | Era | gr/ file | Key Feature |
|---|---|---|---|---|
| NV_04–NV_40 | Celsius/Kelvin/Rankine | 1998–2004 | `nv04.c`, `nv40.c` | Fixed function + early shaders |
| NV_50 (G80) | Tesla | 2006 | `nv50.c` | Unified shader arch, new ISA |
| GF100–119 | Fermi | 2010 | `gf100.c`, `gf117.c` | CUDA 2.0, ECC, context switches |
| GK104–208 | Kepler | 2012 | `gk104.c`, `gk208.c` | GPU Boost, multi-process svc |
| GM107–200 | Maxwell | 2014 | `gm107.c`, `gm200.c` | Unified L2, reduced power |
| GP100–108 | Pascal | 2016 | `gp100.c`, `gp108.c` | HBM2, NVLink, ATS |
| GV100 | Volta | 2017 | `gv100.c` | Tensor cores, independent thread |
| TU102–117 | Turing | 2018 | `tu102.c`, `tu117.c` | RT cores, DLSS, mesh shaders |
| GA100–107 | Ampere | 2020 | `ga102.c`, `ga107.c` | 3rd gen tensor, multi-inst GPU |
| GH100 | Hopper | 2022 | (ga102-based) | Transformer Engine, NVLink 4 |
| AD102–107 | Ada Lovelace | 2022 | (ga102-based) | 4th gen RT, DLSS3 |
| GB10x/GB20x | Blackwell | 2024 | (evolving) | NVLink-C2C, FP4 |

---

## 6. Key Source Files Quick Reference

### DRM / nouveau layer

| File | Purpose |
|---|---|
| `nouveau_drm.c` | PCI probe, `nouveau_drm_probe()`, DRM driver struct |
| `nouveau_drv.h` | `nouveau_drm`, `nouveau_cli` root structs |
| `nouveau_chan.c` | `nouveau_channel` alloc/free, push-buffer setup |
| `nouveau_dma.h` | `RING_SPACE`, `OUT_RING`, `FIRE_RING` macros |
| `nouveau_bo.c` | TTM buffer objects, VRAM/GART placement, eviction |
| `nouveau_gem.c` | GEM ioctl handlers (`gem_new`, `gem_pushbuf`, `gem_info`) |
| `nouveau_vmm.c` | `nouveau_vmm`, VMA alloc/map/unmap |
| `nouveau_fence.c` | GPU fence driver, IRQ → `dma_fence_signal` |
| `nouveau_display.c` | KMS registration, vblank, pageflip |
| `nouveau_abi16.c` | Legacy ABI16 ioctl shim (`CHANNEL_ALLOC`, `GROBJ_ALLOC`) |
| `nouveau_exec.c` | Modern `NOUVEAU_EXEC` ioctl (VM_BIND path) |
| `nouveau_uvmm.c` | `NOUVEAU_VM_BIND` unified VA management |
| `nouveau_sched.c` | `drm_gpu_scheduler` wrapper for EXEC jobs |
| `nouveau_accel.c` | Acceleration channel init, copy engine setup |

### NVKM layer

| File | Purpose |
|---|---|
| `nvkm/core/object.c` | nvkm_object lifecycle (init/fini/mthd) |
| `nvkm/core/subdev.c` | nvkm_subdev use/refcount management |
| `nvkm/core/engine.c` | nvkm_engine channel context management |
| `nvkm/engine/device/base.c` | Per-chip constructor tables (all `*_chipset`) |
| `nvkm/engine/fifo/chan.c` | `nvkm_chan` create/destroy |
| `nvkm/engine/fifo/runl.c` | Runlist / TSG management |
| `nvkm/engine/gr/gf100.c` | Fermi+ graphics engine (largest file) |
| `nvkm/engine/gr/gk104.c` | Kepler graphics engine |
| `nvkm/engine/gr/tu102.c` | Turing graphics engine |
| `nvkm/engine/disp/dp.c` | DisplayPort link training |
| `nvkm/subdev/mmu/vmm.c` | GPU page table management |
| `nvkm/subdev/gsp/ga102.c` | GSP-RM firmware for Ampere |
| `nvkm/falcon/base.c` | Falcon microcontroller framework |

### Display

| File | Purpose |
|---|---|
| `dispnv50/core.c` | NV50+ core display channel |
| `dispnv50/head.c` | Per-head (CRTC) state |
| `dispnv50/atom.h` | Atomic state structs for all display objects |
| `dispnv50/dp.c` | DP training, HPD, link management |
| `dispnv50/hdmi.c` | HDMI audio, infoframes, scrambling |

---

## 7. IOCTL Surface

| IOCTL | Cmd | Purpose | Path |
|---|---|---|---|
| `NOUVEAU_GETPARAM` | 0x00 | Query chipset ID, VRAM size, bus type | `nouveau_abi16_ioctl_getparam` |
| `NOUVEAU_CHANNEL_ALLOC` | 0x02 | Allocate GPU push-buffer channel | `nouveau_abi16_ioctl_channel_alloc` |
| `NOUVEAU_CHANNEL_FREE` | 0x03 | Release channel | `nouveau_abi16_ioctl_channel_free` |
| `NOUVEAU_NVIF` | 0x07 | NVIF object method passthrough | `nvkm_ioctl` |
| `NOUVEAU_GEM_NEW` | 0x40 | Allocate GEM buffer object | `nouveau_gem_ioctl_new` |
| `NOUVEAU_GEM_PUSHBUF` | 0x41 | Submit GPU command push-buffer | `nouveau_gem_ioctl_pushbuf` |
| `NOUVEAU_GEM_CPU_PREP` | 0x42 | Wait for GPU idle on BO (CPU access) | `nouveau_gem_ioctl_cpu_prep` |
| `NOUVEAU_GEM_CPU_FINI` | 0x43 | Release CPU-side BO lock | `nouveau_gem_ioctl_cpu_fini` |
| `NOUVEAU_GEM_INFO` | 0x44 | Query BO placement/address | `nouveau_gem_ioctl_info` |
| `NOUVEAU_VM_INIT` | 0x10 | Init unified VM (VM_BIND mode) | `nouveau_uvmm_ioctl_vm_init` |
| `NOUVEAU_VM_BIND` | 0x11 | Map/unmap VA ranges (VM_BIND) | `nouveau_uvmm_ioctl_vm_bind` |
| `NOUVEAU_EXEC` | 0x12 | Execute with VM_BIND syncobjs | `nouveau_exec_ioctl_exec` |

---

## 8. Power Management Summary

```
NVKM subdev: nvkm/subdev/therm/   — thermal sensor polling, fan control
             nvkm/subdev/volt/    — voltage regulator (GPIO / I2C PMIC)
             nvkm/subdev/clk/     — clock management (gf100, gk104, gm20x…)
             nvkm/subdev/pmu/     — PMU firmware, DVFS, perf levels

Runtime PM:
  nouveau_drm_suspend_late()
    ├─ nouveau_display_fini()    — disable display
    ├─ nvkm_device_fini(suspend) — fini all subdevs in reverse order
    └─ pci_set_power_state(D3)

Dynamic reclocking:
  nouveau_sysfs clocks    → nvkm_clk_set_pstate()
                              └─ per-gen: gk104_clk_pstate_prog()
```

---

## References

- `drivers/gpu/drm/nouveau/nouveau_drm.c` — PCI driver, `nouveau_drm_probe`
- `drivers/gpu/drm/nouveau/nvkm/engine/device/base.c` — chip descriptor tables
- `drivers/gpu/drm/nouveau/nvkm/engine/fifo/chan.c` — channel management
- `drivers/gpu/drm/nouveau/nvkm/engine/gr/gf100.c` — graphics engine (Fermi+)
- `drivers/gpu/drm/nouveau/nouveau_chan.c` — push-buffer channel
- `drivers/gpu/drm/nouveau/nouveau_gem.c` — GEM/pushbuf ioctl
- `drivers/gpu/drm/nouveau/nouveau_vmm.c` — GPU virtual address management
- `drivers/gpu/drm/nouveau/dispnv50/core.c` — NV50+ atomic display core
- `include/uapi/drm/nouveau_drm.h` — UAPI ioctl definitions
