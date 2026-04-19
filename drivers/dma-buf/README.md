# Linux Kernel: DMA-BUF Subsystem

> Source: `drivers/dma-buf/` — noble-linux-oem (oem-6.17-next)

---

## 1. Subsystem Stack

```
┌─────────────────────────────────────────────────────────────────┐
│                        USERSPACE                                │
│  GPU app / camera / video encoder / display compositor          │
│  (libdrm, V4L2, GStreamer, Vulkan, Wayland compositor)          │
└───────────────────────┬─────────────────────────────────────────┘
                        │  open/ioctl/mmap  (file descriptor)
                        ▼
┌─────────────────────────────────────────────────────────────────┐
│                  UAPI / VFS LAYER                               │
│  /dev/dma_heap/<name>  →  DMA_HEAP_IOCTL_ALLOC                 │
│  /dev/dma_buf fd       →  DMA_BUF_IOCTL_SYNC                   │
│                             DMA_BUF_IOCTL_EXPORT_SYNC_FILE      │
│                             DMA_BUF_IOCTL_IMPORT_SYNC_FILE      │
│  sync_file fd          →  poll() / SYNC_IOC_FILE_INFO           │
└────────┬──────────────────────────┬────────────────────────────┘
         │                          │
         ▼                          ▼
┌────────────────┐       ┌──────────────────────────┐
│   dma-heap.c   │       │      sync_file.c          │
│  Heap manager  │       │  Sync file (fence→fd)     │
│  (char dev)    │       │  used by Android/Vulkan   │
│  ┌──────────┐  │       └──────────────────────────┘
│  │system    │  │                  │
│  │heap.c    │  │                  │
│  ├──────────┤  │                  │
│  │cma_heap  │  │                  ▼
│  │.c        │  │    ┌──────────────────────────────┐
│  └────┬─────┘  │    │         dma-fence.c           │
└───────┼────────┘    │  Async GPU/DMA sync primitive │
        │             │  ┌──────────────────────────┐ │
        │             │  │ dma-fence-array.c        │ │
        │             │  │ dma-fence-chain.c        │ │
        │             │  │ dma-fence-unwrap.c       │ │
        │             │  └──────────────────────────┘ │
        │             └──────────────┬───────────────┘
        │                           │
        ▼                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                       dma-buf.c  (CORE)                         │
│                                                                 │
│  dma_buf_export()   – wrap private buffer → dma_buf + fd        │
│  dma_buf_attach()   – importer registers interest               │
│  dma_buf_map_attachment() – get sg_table for DMA                │
│  dma_buf_begin/end_cpu_access() – coherency sync                │
│  dma_buf_move_notify() – driver migration callback              │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                       dma-resv.c                                │
│  Reservation object: tracks shared/exclusive fences per buffer  │
│  Uses ww_mutex (wound-wait) to prevent deadlock across devices  │
│  dma_resv_add_fence() / dma_resv_wait_timeout()                │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│             Physical Memory / IOMMU / DMA hardware              │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. Components

### 2.1 `dma-buf.c` — Core Buffer Sharing

The heart of the subsystem. A **dma_buf** is a reference-counted kernel object
backed by an anonymous file. It acts as a "passport" letting multiple devices
share the same physical pages without copying.

| Function | Role |
|---|---|
| `dma_buf_export()` | Exporter (e.g., GPU driver) wraps private memory → returns `dma_buf*` |
| `dma_buf_fd()` | Convert `dma_buf*` → user-visible file descriptor |
| `dma_buf_get()` | Importer gets `dma_buf*` from fd |
| `dma_buf_attach()` | Importer registers an attachment (device + ops) |
| `dma_buf_map_attachment()` | Returns `sg_table` suitable for device DMA |
| `dma_buf_begin_cpu_access()` | Cache flush/invalidate before CPU read/write |
| `dma_buf_end_cpu_access()` | Cache writeback after CPU access |
| `dma_buf_move_notify()` | Tell importers buffer is being migrated |

**Exporter ops** (`dma_buf_ops`): `attach`, `detach`, `map_dma_buf`,
`unmap_dma_buf`, `release`, `begin_cpu_access`, `end_cpu_access`, `mmap`.

### 2.2 `dma-fence.c` — Asynchronous Synchronization

A **dma_fence** is a one-shot, GPU-context-aware semaphore. Once signaled it
never goes back. Each fence belongs to a **context** (a u64 ID); fences within
the same context are totally ordered.

- `dma_fence_init()` — allocate & assign to a context
- `dma_fence_signal()` — mark GPU work done, wake waiters
- `dma_fence_wait()` — block (or poll) until signaled
- `dma_fence_add_callback()` — fire a callback when signaled

**Variants:**
- `dma-fence-array.c` — wait for *all* fences in an array
- `dma-fence-chain.c` — ordered chain of fences (pipeline stages)
- `dma-fence-unwrap.c` — flatten nested arrays/chains for iteration

### 2.3 `dma-resv.c` — Reservation Object

A **dma_resv** is attached 1:1 to each `dma_buf`. It stores a list of pending
fences split by *usage*:

| Usage | Meaning |
|---|---|
| `DMA_RESV_USAGE_KERNEL` | Kernel-internal (e.g., TTM migration) |
| `DMA_RESV_USAGE_WRITE` | Exclusive writer fence |
| `DMA_RESV_USAGE_READ` | Shared reader fences |
| `DMA_RESV_USAGE_BOOKKEEP` | Tracking only, not waited on |

Uses **ww_mutex** (wound-wait mutex) to allow multiple devices to lock buffers
in parallel without deadlock.

### 2.4 `dma-heap.c` + `heaps/` — Heap Allocator

Userspace allocates DMA buffers by opening `/dev/dma_heap/<name>` and calling
`DMA_HEAP_IOCTL_ALLOC`. The result is a dma_buf fd ready for sharing.

| Heap | Source |
|---|---|
| `system` (`system_heap.c`) | Highmem pages via page allocator |
| `system-uncached` | Same, but uncached mappings |
| `linux,cma` (`cma_heap.c`) | Contiguous Memory Allocator region |

### 2.5 `sync_file.c` — Userspace Fence Sharing

`sync_file` wraps a `dma_fence` into a file descriptor that userspace can
pass between processes (used by Android, Vulkan timeline semaphores, Wayland
`linux-drm-syncobj`).

- `sync_file_create()` — fence → fd
- `sync_file_get_fence()` — fd → fence  
- `sync_file_merge()` — combine two sync files

### 2.6 `udmabuf.c` — Userspace-backed DMA Buffer

Creates a dma_buf backed by user-allocated `memfd` pages. Used by QEMU/KVM
for zero-copy guest→host buffer sharing.

### 2.7 `sw_sync.c` — Software Sync (Testing)

Provides a fake GPU timeline via `/dev/sw_sync` for testing fence flows without
real hardware.

---

## 3. Data Flow: GPU Render → Display

```
 GPU Driver (exporter)           Display Driver (importer)
 ─────────────────────           ─────────────────────────
 1. Allocate GEM buffer
    (private memory)
         │
 2. dma_buf_export()
    ┌────▼────┐
    │ dma_buf │◄──── anonymous fd sent via SCM_RIGHTS / DRM PRIME
    └────┬────┘
         │  userspace passes fd to compositor (Wayland)
         │
 3. compositor calls                4. dma_buf_attach(dev=display)
    dma_buf_get(fd) ──────────────► dma_buf_map_attachment()
                                       └─► sg_table (physical pages)
                                           IOMMU maps for display engine
         │
 5. GPU submits work               6. dma_resv_add_fence(WRITE)
    creates dma_fence ─────────────► stored in dma_buf.resv
         │
 7. Display driver:                8. dma_resv_wait_timeout()
    wait for GPU done  ◄────────────   blocks until fence signaled
         │
 9. dma_fence_signal()             10. Display engine scans out buffer
    (GPU interrupt)  ──────────────►    (zero copy, no CPU involved)
```

---

## 4. Implicit vs Explicit Fencing

```
IMPLICIT FENCING                    EXPLICIT FENCING
────────────────                    ─────────────────
dma_resv stores fences              sync_file fd passed between processes
automatically per buffer            (Android HWC2, Vulkan VK_KHR_external_fence,
                                     DRM drm_syncobj)

dma_buf_begin_cpu_access()          DMA_BUF_IOCTL_EXPORT_SYNC_FILE
  └─ waits resv fences              DMA_BUF_IOCTL_IMPORT_SYNC_FILE
  └─ CPU gets coherent view         Userspace manages fence lifetime
```

---

## 5. Key Kernel Data Structures

```c
struct dma_buf {
    size_t size;
    struct file *file;            // anonymous VFS file (= the fd)
    struct list_head attachments; // list of dma_buf_attachment
    const struct dma_buf_ops *ops;// exporter callbacks
    struct dma_resv *resv;        // fence tracking
    const char *name;             // debug label
    struct list_head list_node;   // global dmabuf_list
};

struct dma_buf_attachment {
    struct dma_buf *dmabuf;
    struct device *dev;           // importer's device
    const struct dma_buf_attach_ops *importer_ops;
    struct sg_table *sgt;         // cached scatter-gather table
};

struct dma_fence {
    spinlock_t *lock;
    const struct dma_fence_ops *ops;
    u64 context;                  // which engine/context
    u64 seqno;                    // monotonic sequence within context
    unsigned long flags;          // DMA_FENCE_FLAG_SIGNALED_BIT, etc.
    struct list_head cb_list;     // callbacks to fire on signal
};

struct dma_resv {
    struct ww_mutex lock;
    struct dma_resv_list __rcu *fences; // RCU list of (fence, usage) pairs
};
```

---

## 6. Trace Events (bpftrace hooks)

| Tracepoint | Fires when |
|---|---|
| `dma_fence:dma_fence_init` | New fence created |
| `dma_fence:dma_fence_emit` | Fence emitted by driver |
| `dma_fence:dma_fence_enable_signal` | First waiter registered |
| `dma_fence:dma_fence_signaled` | GPU work completed |
| `dma_fence:dma_fence_wait_start` | Thread begins waiting |
| `dma_fence:dma_fence_wait_end` | Thread done waiting |
| `dma_fence:dma_fence_destroy` | Fence freed |

---

## 7. Summary

The DMA-BUF subsystem solves three problems in a unified framework:

1. **Zero-copy sharing** — physical pages pass between GPU, camera, display, CPU as a file descriptor; no memcpy needed.
2. **Cross-device synchronization** — `dma_fence` + `dma_resv` ensure a GPU render is complete before the display engine scans out.
3. **Userspace control** — `sync_file` and `dma-heap` give applications direct, portable access to both allocation and synchronization.
