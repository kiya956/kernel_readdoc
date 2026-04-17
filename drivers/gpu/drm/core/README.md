# DRM Core Subsystem — Deep Dive Analysis

> **Source tree:** `drivers/gpu/drm/`
> **Kernel:** noble-linux-oem (oem-6.17-next)
> **Date:** 2026-04-17

---

## 1. Full Subsystem Stack

```
╔══════════════════════════════════════════════════════════════════╗
║                    USER SPACE                                    ║
║  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────────────┐  ║
║  │  X11 /   │  │ Wayland  │  │  Vulkan  │  │  OpenGL / EGL  │  ║
║  │  Xwayland│  │Compositor│  │   ICD    │  │  Mesa / libGL  │  ║
║  └────┬─────┘  └────┬─────┘  └────┬─────┘  └───────┬────────┘  ║
║       │             │              │                 │           ║
║       └─────────────┴──────┬───────┴─────────────────┘          ║
║                            │ libdrm  (ioctl wrappers)           ║
╚════════════════════════════╪═════════════════════════════════════╝
                             │  open / ioctl / read / poll / mmap
╔════════════════════════════╪═════════════════════════════════════╗
║         KERNEL — VFS layer │                                     ║
║   /dev/dri/card0           │      /dev/dri/renderD128            ║
║   (primary minor)          ↓      (render minor)                 ║
║  ┌─────────────────────────────────────────────────────────────┐ ║
║  │                   drm_file  (per-fd)                        │ ║
║  │  object_idr  │  event_list  │  master ref  │  client caps   │ ║
║  └──────────────────────────┬──────────────────────────────────┘ ║
║                             │                                    ║
║  ┌──────────────────────────▼──────────────────────────────────┐ ║
║  │                   drm_ioctl  (dispatcher)                   │ ║
║  │   drm_ioctls[128]  ──►  permit check  ──►  copy_from_user  │ ║
║  │       ▼ handler          ▼ returns          copy_to_user   │ ║
║  └──────────────────────────┬──────────────────────────────────┘ ║
║            ┌────────────────┼──────────────────────┐            ║
║            ▼                ▼                      ▼            ║
║  ┌──────────────┐  ┌──────────────────┐  ┌──────────────────┐  ║
║  │  drm_gem     │  │  drm_atomic /    │  │  drm_syncobj /   │  ║
║  │  (memory)    │  │  drm_vblank      │  │  drm_prime       │  ║
║  │              │  │  (display timing)│  │  (sync / share)  │  ║
║  └──────┬───────┘  └────────┬─────────┘  └────────┬─────────┘  ║
║         │                   │                      │            ║
║  ┌──────▼───────────────────▼──────────────────────▼──────────┐ ║
║  │                 drm_device  (global state)                  │ ║
║  │  mode_config │ vblank[] │ mm │ master │ debugfs │ minors   │ ║
║  └──────────────────────────┬──────────────────────────────────┘ ║
╚════════════════════════════╪═════════════════════════════════════╝
                             │  driver callbacks (drm_driver.*)
╔════════════════════════════╪═════════════════════════════════════╗
║        HARDWARE DRIVER     │                                     ║
║  ┌──────────────────────── ▼ ──────────────────────────────────┐ ║
║  │  i915 / amdgpu / nouveau / msm / …                         │ ║
║  │  gem_create  │  gem_mmap  │  mode_set  │  irq_handler       │ ║
║  └──────────────────────────┬──────────────────────────────────┘ ║
╚════════════════════════════╪═════════════════════════════════════╝
                             │  PCIe / MMIO / DMA
╔════════════════════════════╪═════════════════════════════════════╗
║        HARDWARE            ▼                                     ║
║  [ GPU Die ]  [ Display Engine ]  [ Video Memory ]  [ IRQ ]     ║
╚══════════════════════════════════════════════════════════════════╝
```

---

## 2. Layer-by-layer Component Explanation

### Layer 0 — Hardware

| Component | Role |
|---|---|
| GPU Die | Executes shaders, rasterization, compute |
| Display Engine | Scanout CRTC+planes, vblank generation |
| Video Memory (VRAM) | Framebuffers, command rings |
| IRQ lines | Vblank, page-flip completion, error signaling |

---

### Layer 1 — Hardware Driver (e.g. i915, amdgpu)

Implements the `drm_driver` vtable:

| Callback | Purpose |
|---|---|
| `.gem_create_object` | Allocate driver-private GEM object |
| `.gem_prime_import_sg_table` | Import DMA-BUF |
| `.irq_handler` | Handle GPU/display interrupts |
| `.ioctls[]` | Driver-specific ioctls |
| `.fops` | Override file operations if needed |

---

### Layer 2 — drm_device (global state)

Central hub owned by one physical device:

```
struct drm_device {
    struct drm_driver        *driver;     // vtable
    struct drm_mode_config    mode_config;// KMS objects
    struct drm_vblank_crtc   *vblank;    // per-CRTC vblank
    struct drm_mm             vma_offset_manager; // mmap
    struct list_head          filelist;  // open drm_files
    struct drm_master        *master;   // display authority
    struct drm_minor         *primary;  // /dev/dri/card*
    struct drm_minor         *render;   // /dev/dri/renderD*
    ...
}
```

---

### Layer 3 — drm_file (per file-descriptor)

Each `open()` creates one:

```
struct drm_file {
    struct idr     object_idr;        // handle → gem_object
    spinlock_t     table_lock;
    struct list_head event_list;      // pending async events
    struct list_head pending_event_list;
    wait_queue_head_t event_wait;
    struct drm_master *master;        // NULL for render clients
    u64 client_caps;                  // DRM_CLIENT_CAP_* bits
    bool authenticated;
    bool is_master;
    ...
}
```

---

### Layer 4 — drm_ioctl (dispatcher)

```
drm_ioctl()
  │
  ├─ find descriptor in drm_ioctls[] (core) or driver->ioctls[]
  ├─ drm_ioctl_permit()  →  check DRM_AUTH / DRM_MASTER / DRM_ROOT_ONLY
  ├─ copy_from_user()
  ├─ call handler(dev, data, file_priv)
  └─ copy_to_user()
```

IOCTL permission model:

| Flag | Requirement |
|---|---|
| `DRM_ROOT_ONLY` | `CAP_SYS_ADMIN` |
| `DRM_MASTER` | file is DRM master |
| `DRM_AUTH` | authenticated or render client |
| `DRM_RENDER_ALLOW` | allowed on render node |

---

### Layer 5 — GEM (Graphics Execution Manager)

Memory object lifecycle:

```
drm_gem_object
  ├─ kref refcount
  ├─ handle_count (userspace references)
  ├─ dma_resv *resv       ← fences / locks
  ├─ struct file *filp    ← shmem backing (optional)
  ├─ drm_vma_offset_node  ← mmap offset
  └─ driver-private data
```

Handle table (per drm_file):
```
handle (u32)  ──IDR──►  drm_gem_object  ──kref──►  actual pages
```

---

### Layer 6 — KMS / Atomic Modeset

Object hierarchy:

```
drm_device.mode_config
  ├─ drm_connector[]   (HDMI-A-1, DP-1, …)  — physical output
  ├─ drm_encoder[]     (bridges the gap)
  ├─ drm_crtc[]        (timing generator)
  └─ drm_plane[]       (primary / overlay / cursor)
```

Atomic commit flow:

```
BUILD state:
  drm_atomic_state_alloc()
  drm_atomic_get_crtc_state()
  drm_atomic_get_plane_state()
  drm_atomic_get_connector_state()

VALIDATE:
  drm_atomic_check_only()  →  driver.atomic_check()

COMMIT:
  drm_atomic_commit()
    ├─ drm_atomic_helper_prepare_planes()
    ├─ drm_atomic_helper_commit_hw_done()   → hw_done completion
    └─ flip_done after vblank interrupt     → flip_done completion
```

---

### Layer 7 — Vblank Subsystem

```
Hardware interrupt (VBLANK)
  │
  └─► drm_handle_vblank(dev, pipe)
        ├─ update vblank counter & timestamp
        ├─ wake drm_wait_vblank() callers
        └─ deliver pending events to drm_file.event_list
              │
              └─► userspace reads via drm_read()
```

---

### Layer 8 — Sync Primitives

| Mechanism | Purpose |
|---|---|
| `dma_fence` | Signal GPU work completion |
| `dma_resv` | Per-object shared/exclusive fence sets |
| `drm_syncobj` | Userspace-visible sync point (exportable to FD) |
| PRIME / DMA-BUF | Share GEM objects between drivers/processes |

---

## 3. Data Flow Diagrams

### 3a. Typical Render Frame (GPU path)

```
 Userspace                      Kernel DRM Core            Hardware Driver
     │                                │                          │
     │  ioctl(GEM_CREATE, size)       │                          │
     ├──────────────────────────────► │                          │
     │                                │ gem_create_object()       │
     │                                ├─────────────────────────►│
     │                                │◄── drm_gem_object ───────┤
     │◄── handle (u32) ───────────────┤                          │
     │                                │                          │
     │  ioctl(GEM_MMAP, handle)       │                          │
     ├──────────────────────────────► │                          │
     │◄── mmap_offset ────────────────┤                          │
     │                                │                          │
     │  mmap(offset, size)            │                          │
     ├──────────────────────────────► │ fault handler maps pages │
     │◄── userspace VA ───────────────┤                          │
     │                                │                          │
     │  [write commands to buf]       │                          │
     │                                │                          │
     │  ioctl(EXECBUF / submit)       │                          │
     ├──────────────────────────────► │                          │
     │                                │ driver submit            │
     │                                ├─────────────────────────►│
     │                                │                 GPU runs │
     │◄── fence fd ───────────────────┤◄── dma_fence ────────────┤
     │                                │                          │
     │  poll(fence_fd, POLLIN)        │                          │
     ├──────────────────────────────► │                          │
     │◄── ready ──────────────────────┤◄── fence_signal() ───────┤
```

### 3b. Atomic Page Flip (Display path)

```
 Compositor                     DRM Core                    Display HW
     │                              │                           │
     │  ioctl(ATOMIC, flags=FLIP)   │                           │
     ├─────────────────────────────►│                           │
     │                              │ atomic_check()            │
     │                              │ atomic_commit()           │
     │                              │ program registers         │
     │                              ├──────────────────────────►│
     │                              │              vblank irq   │
     │                              │◄──────────────────────────┤
     │                              │ drm_handle_vblank()       │
     │                              │ queue PAGE_FLIP event     │
     │                              │                           │
     │  read(drm_fd, &ev)           │                           │
     ├─────────────────────────────►│                           │
     │◄── DRM_EVENT_FLIP_COMPLETE ──┤                           │
     │                              │                           │
     │  [render next frame]         │                           │
```

### 3c. Object sharing via PRIME

```
Process A                       DRM Core                  Process B
    │                               │                          │
    │  ioctl(PRIME_HANDLE_TO_FD)    │                          │
    ├──────────────────────────────►│                          │
    │                               │ dma_buf_export()         │
    │◄── dma_buf_fd ────────────────┤                          │
    │                               │                          │
    │  sendmsg(socket, dma_buf_fd)  │                          │
    ├──────────────────────────────────────────────────────────►│
    │                               │                          │
    │                               │  ioctl(PRIME_FD_TO_HANDLE)│
    │                               │◄─────────────────────────┤
    │                               │ dma_buf_get()            │
    │                               │ gem_prime_import()       │
    │                               ├─────────────────────────►│
    │                               │◄── local handle ─────────┤
```

---

## 4. Key Source Files Quick Reference

| File | Lines | Purpose |
|---|---|---|
| `drm_drv.c` | ~700 | Device alloc / register / unplug |
| `drm_file.c` | ~700 | Per-fd lifecycle, events, fdinfo |
| `drm_ioctl.c` | ~700 | IOCTL table + dispatch |
| `drm_gem.c` | ~1000 | GEM base object management |
| `drm_vblank.c` | ~1400 | Vblank IRQ, timestamps, events |
| `drm_atomic.c` | ~2000 | Atomic state machine |
| `drm_atomic_uapi.c` | ~1500 | Userspace ↔ atomic state bridge |
| `drm_prime.c` | ~600 | PRIME / DMA-BUF import/export |
| `drm_syncobj.c` | ~1200 | GPU sync objects / timelines |
| `drm_mm.c` | ~800 | Range allocator for VRAM regions |

---

## 5. Security Model Summary

```
                   ┌────────────────────────┐
                   │  /dev/dri/card0         │  (primary node)
                   │  crw-rw-r-- root video  │
                   └──────────┬─────────────┘
                              │
              ┌───────────────┼────────────────┐
              ▼               ▼                ▼
        DRM_ROOT_ONLY    DRM_MASTER       DRM_AUTH
        CAP_SYS_ADMIN    setmaster /      Authenticated
        (privileged       dropmaster       client
         ioctls)          (KMS owner)      (legacy DRI2)

                   ┌────────────────────────┐
                   │  /dev/dri/renderD128    │  (render node)
                   │  crw-rw-rw- root render │
                   └──────────┬─────────────┘
                              │
                        DRM_RENDER_ALLOW
                        (GPU-only ioctls,
                         no display access)
```

---

## References

- `drivers/gpu/drm/drm_drv.c` — `drm_dev_alloc`, `drm_dev_register`
- `drivers/gpu/drm/drm_file.c` — `drm_open`, `drm_read`, `drm_poll`
- `drivers/gpu/drm/drm_ioctl.c` — `drm_ioctl`, `drm_ioctls[]`
- `drivers/gpu/drm/drm_gem.c` — `drm_gem_object_init`, `drm_gem_handle_create`
- `drivers/gpu/drm/drm_vblank.c` — `drm_handle_vblank`, `drm_wait_vblank_ioctl`
- `drivers/gpu/drm/drm_atomic.c` — `drm_atomic_commit`, `drm_crtc_commit_wait`
- Documentation: `Documentation/gpu/drm-internals.rst`
