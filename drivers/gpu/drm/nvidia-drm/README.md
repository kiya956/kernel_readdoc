# DRM nvidia-drm — NVIDIA Proprietary DRM/KMS Interface

> **Source tree:** `drivers/gpu/drm/nvidia-drm/`
> **Kernel:** noble-linux-oem
> **Date:** 2026-04-28
> **Scanned from:** ~/canonical/kernel/noble-linux-oem

---

## 1. Overview

`nvidia-drm` is the **kernel-side DRM/KMS interface module** for NVIDIA's
proprietary GPU driver. It bridges the standard Linux DRM/KMS framework with
NVIDIA's proprietary `nvidia-modeset` (NVKMS) backend. Unlike nouveau (which is
fully open-source), nvidia-drm relies on the closed-source `nvidia.ko` and
`nvidia-modeset.ko` modules.

The module is named `nvidia-drm.ko` and lives in the kernel source tree under
`drivers/gpu/drm/nvidia-drm/`. It registers as a DRM driver with atomic
modesetting support and provides GEM buffer management, cursor/overlay planes,
fencing, and writeback connectors.

---

## 2. Full Subsystem Stack

```
╔══════════════════════════════════════════════════════════════════════╗
║                       USER SPACE                                     ║
║  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────┐  ║
║  │ X11 / Wayland│  │ CUDA / OpenCL│  │ Vulkan (nvidia_icd)     │  ║
║  │ (libEGL)     │  │              │  │                          │  ║
║  └──────┬───────┘  └──────┬───────┘  └──────────┬───────────────┘  ║
║         └─────────────────┴───────────┬──────────┘                  ║
║                                       │  libdrm + nvidia UMD ioctls  ║
╚═══════════════════════════════════════╪════════════════════════════╝
                                        │  DRM ioctls
╔═══════════════════════════════════════╪════════════════════════════╗
║  KERNEL — nvidia-drm.ko                                            ║
║  ┌────────────────────────────────────▼───────────────────────┐    ║
║  │  nv_drm_driver (nvidia-drm-drv.c:1842)                     │    ║
║  │  .driver_features = DRIVER_GEM | DRIVER_MODESET | ATOMIC   │    ║
║  │  .dumb_create = nv_drm_dumb_create                         │    ║
║  │  .master_set / .master_drop                                │    ║
║  └────────────────────────────────────┬───────────────────────┘    ║
║                                       │                            ║
║  ┌────────────────────────────────────▼───────────────────────┐    ║
║  │  nv_drm_device (nvidia-drm-priv.h:90)                      │    ║
║  │  ┌─────────────────────────────────────────────────────┐   │    ║
║  │  │ dev (*drm_device)      │ gpu_info (nv_gpu_info_t)   │   │    ║
║  │  │ pDevice (*NvKmsKapiDevice) → NVKMS backend handle   │   │    ║
║  │  │ lock (mutex)           │ pitchAlignment              │   │    ║
║  │  │ modifiers[]            │ pageKindGeneration          │   │    ║
║  │  │ hotplug_event_work     │ enable_event_handling       │   │    ║
║  │  └─────────────────────────────────────────────────────┘   │    ║
║  └────────────────────────────────────────────────────────────┘    ║
║                                                                    ║
║  ┌── Display pipeline ──────────────────────────────────────┐      ║
║  │  nv_drm_crtc (nvidia-drm-crtc.h:48)                      │      ║
║  │    nv_drm_plane (L172) — primary / cursor / overlay       │      ║
║  │    nv_drm_plane_state (L240) — extends drm_plane_state    │      ║
║  │    nv_drm_crtc_state (L126) — extends drm_crtc_state      │      ║
║  │    nv_drm_flip (L88) — page flip tracking                 │      ║
║  │                                                            │      ║
║  │  nv_drm_connector (nvidia-drm-connector.h:39)             │      ║
║  │  nv_drm_encoder (nvidia-drm-encoder.h:36)                 │      ║
║  └────────────────────────────────────────────────────────────┘      ║
║                                                                      ║
║  ┌── GEM / Buffer management ──────────────────────────────┐        ║
║  │  nv_drm_gem_object (nvidia-drm-gem.h:63)                 │        ║
║  │  ┌────────────────────────────────────────────────────┐  │        ║
║  │  │ base (drm_gem_object)    │ nv_dev (*nv_drm_device) │  │        ║
║  │  │ ops (*nv_drm_gem_object_funcs) │ pMemory (NVKMS)   │  │        ║
║  │  └────────────────────────────────────────────────────┘  │        ║
║  │  Subtypes:                                                │        ║
║  │    nv_drm_gem_nvkms_memory (NVKMS-allocated VRAM)        │        ║
║  │    nv_drm_gem_user_memory (userptr)                      │        ║
║  │    nv_drm_gem_dma_buf (DMA-BUF import)                   │        ║
║  └──────────────────────────────────────────────────────────┘        ║
║                                                                      ║
║  ┌── Atomic modesetting (nvidia-drm-modeset.c) ────────────┐        ║
║  │  nv_drm_atomic_check (L514) → validate state             │        ║
║  │  nv_drm_atomic_commit (L597) → push to NVKMS              │        ║
║  │  nv_drm_handle_flip_occurred (L832) → vblank/fence signal │        ║
║  └──────────────────────────────────────────────────────────┘        ║
║                                                                      ║
║  ┌── Fencing (nvidia-drm-fence.c) ─────────────────────────┐        ║
║  │  nv_drm_fence_supported_ioctl (L387)                      │        ║
║  │  nv_drm_prime_fence_context_create_ioctl (L458)           │        ║
║  │  nv_drm_gem_prime_fence_attach_ioctl (L515)               │        ║
║  │  nv_drm_semsurf_fence_* — semaphore surface fences        │        ║
║  └──────────────────────────────────────────────────────────┘        ║
║                                                                      ║
║         │ NVKMS KAPI (Kernel API)                                    ║
║         ▼                                                            ║
║  ┌── nvidia-modeset.ko (closed-source) ─────────────────────┐       ║
║  │  NvKmsKapi* functions                                     │       ║
║  │  Display hardware programming                             │       ║
║  └──────────────────────────────────────────────────────────┘       ║
║         │                                                            ║
║         ▼                                                            ║
║  ┌── nvidia.ko (closed-source) ─────────────────────────────┐       ║
║  │  GPU engine management, memory allocation, compute        │       ║
║  └──────────────────────────────────────────────────────────┘       ║
╚════════════════════════════════════════════════════════════════════╝
                             │  PCIe BAR / MMIO
╔════════════════════════════╪════════════════════════════════════════╗
║  HARDWARE                  ▼                                        ║
║  NVIDIA GPUs: Kepler → Maxwell → Pascal → Volta → Turing →         ║
║  Ampere → Ada Lovelace → Hopper → Blackwell                        ║
║  [ VRAM (GDDR6/HBM) ] [ Display engines ] [ NVENC/NVDEC ]          ║
╚════════════════════════════════════════════════════════════════════╝
```

---

## 3. Module Dependencies

```
nvidia-drm.ko  ──depends-on──►  nvidia-modeset.ko  ──depends-on──►  nvidia.ko
     │                                │                                │
     │ DRM/KMS interface              │ Display/modesetting            │ Core GPU
     │ GEM buffers                    │ NVKMS KAPI                     │ PCIe/MMIO
     │ Atomic commit                  │                                │ Memory mgmt
```

All three modules must be loaded. `nvidia-drm` calls into `nvidia-modeset`
via the `nvKms` function pointer table (KAPI — Kernel API).

---

## 4. Workflow: Atomic Modeset Commit

```
 Userspace (Wayland/X11)          nvidia-drm.ko                  nvidia-modeset.ko
      │                               │                               │
      │  drmModeAtomicCommit()        │                               │
      ├──────────────────────────────►│                               │
      │                               │  nv_drm_atomic_check (L514)  │
      │                               │  → validate planes, CRTCs    │
      │                               │                               │
      │                               │  nv_drm_atomic_commit (L597) │
      │                               │  → translate to NVKMS request│
      │                               ├──────────────────────────────►│
      │                               │  NvKmsKapi::applyModesetConfig│
      │                               │                               │
      │                               │  (display hardware programmed)│
      │                               │◄──────────────────────────────┤
      │                               │                               │
      │                               │  nv_drm_handle_flip_occurred │
      │                               │  (L832) → vblank event        │
      │◄── vblank/flip done ──────────┤                               │
```

---

## 5. Key Source Files

| File | Purpose |
|---|---|
| `nvidia-drm.c` | Module init: `nv_drm_init` (L44) / `nv_drm_exit` (L61) |
| `nvidia-drm-linux.c` | Linux module_init/exit wrappers |
| `nvidia-drm-drv.c` | DRM driver struct (L1842), device register (L1968), probe (L2108) |
| `nvidia-drm-priv.h` | `nv_drm_device` (L90) — central device struct |
| `nvidia-drm-crtc.c` | CRTC/plane setup (~3K lines), atomic helpers, CRC |
| `nvidia-drm-crtc.h` | `nv_drm_crtc` (L48), `nv_drm_plane` (L172), `nv_drm_plane_state` (L240) |
| `nvidia-drm-connector.c/h` | Connector: hotplug, EDID, DPMS |
| `nvidia-drm-encoder.c/h` | Encoder wrappers |
| `nvidia-drm-modeset.c` | Atomic check (L514) / commit (L597) / flip_occurred (L832) |
| `nvidia-drm-gem.c/h` | GEM object: `nv_drm_gem_object` (L63), mmap (L250), free (L47) |
| `nvidia-drm-gem-nvkms-memory.c/h` | NVKMS-backed GEM (VRAM allocation) |
| `nvidia-drm-gem-user-memory.c/h` | Userptr GEM objects |
| `nvidia-drm-gem-dma-buf.c/h` | DMA-BUF import/export |
| `nvidia-drm-fb.c/h` | Framebuffer: `nv_drm_framebuffer` (L38) |
| `nvidia-drm-fence.c/h` | Fencing: prime fences, semaphore surfaces |
| `nvidia-drm-format.c/h` | Pixel format conversion tables |
| `nvidia-drm-helper.c/h` | Kernel version compatibility helpers |
| `nvidia-drm-os-interface.c/h` | OS abstraction (memory, timers) |
| `nvidia-drm-utils.c/h` | Utility functions |
| `nv-pci-table.c/h` | PCI device ID table (MODULE_DEVICE_TABLE) |
| `nv-kthread-q.c` | Kernel thread work queue |

---

## References

- `nvidia-drm-priv.h:90` — `struct nv_drm_device`
- `nvidia-drm-gem.h:63` — `struct nv_drm_gem_object`
- `nvidia-drm-crtc.h:48` — `struct nv_drm_crtc`
- `nvidia-drm-drv.c:1842` — `nv_drm_driver`
- `nvidia-drm-drv.c:1968` — `nv_drm_register_drm_device`
- `nvidia-drm-modeset.c:514` — `nv_drm_atomic_check`
- `nvidia-drm-modeset.c:597` — `nv_drm_atomic_commit`
- `nvidia-drm.c:44` — `nv_drm_init`
