# DRM TTM (Translation Table Manager) — Deep Dive Analysis

> **Source tree:** `drivers/gpu/drm/ttm/`
> **Kernel:** noble-linux-oem
> **Date:** 2026-04-28
> **Scanned from:** ~/canonical/kernel/noble-linux-oem

---

## 1. Full Subsystem Stack

```
╔══════════════════════════════════════════════════════════════════════╗
║                        USER SPACE                                    ║
║  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────────────────┐  ║
║  │  Mesa    │  │  Vulkan  │  │ Compute  │  │   Video (VAAPI)    │  ║
║  │  (GL)    │  │  (ICD)   │  │ (OpenCL) │  │                    │  ║
║  └────┬─────┘  └────┬─────┘  └────┬─────┘  └───────┬────────────┘  ║
║       └─────────────┴──────┬───────┴─────────────────┘              ║
║                            │  libdrm (GEM / BO ioctls)               ║
╚════════════════════════════╪════════════════════════════════════════╝
                             │  ioctl (GEM_CREATE / MAP / EXEC)
╔════════════════════════════╪════════════════════════════════════════╗
║  KERNEL — DRM Driver (amdgpu / radeon / nouveau / vmwgfx / xe)      ║
║  ┌─────────────────────────▼──────────────────────────────────────┐ ║
║  │  Driver BO wrapper (e.g. amdgpu_bo embeds ttm_buffer_object)   │ ║
║  └──────────────────────────┬─────────────────────────────────────┘ ║
║                             │                                       ║
║  ┌──────────────────────────▼─────────────────────────────────────┐ ║
║  │           TTM — Translation Table Manager                       ║
║  │                                                                  ║
║  │  ttm_buffer_object (ttm_bo.h:101)                               ║
║  │  ┌──────────────────────────────────────────────────────┐       ║
║  │  │ base (drm_gem_object)  │ bdev (*ttm_device)          │       ║
║  │  │ type (ttm_bo_type)     │ page_alignment              │       ║
║  │  │ kref (refcount)        │ resource (*ttm_resource)    │       ║
║  │  │ ttm (*ttm_tt)          │ deleted (bool)              │       ║
║  │  │ pin_count              │ priority                    │       ║
║  │  │ bulk_move              │ sg (*sg_table)              │       ║
║  │  │ delayed_delete (work)  │ destroy (callback)          │       ║
║  │  └──────────────────────────────────────────────────────┘       ║
║  │                                                                  ║
║  │  ttm_resource (ttm_resource.h:253)                              ║
║  │  ┌──────────────────────────────────────────────────────┐       ║
║  │  │ start (unsigned long)  │ size                        │       ║
║  │  │ mem_type (u32)         │ placement (u32)             │       ║
║  │  │ bus (ttm_bus_placement)│ bo (*ttm_buffer_object)     │       ║
║  │  │ lru (ttm_lru_item)    │ css (*dmem_cgroup)          │       ║
║  │  └──────────────────────────────────────────────────────┘       ║
║  │                                                                  ║
║  │  ttm_resource_manager (ttm_resource.h:189)                      ║
║  │  ┌──────────────────────────────────────────────────────┐       ║
║  │  │ func (*ttm_resource_manager_func)                    │       ║
║  │  │ use_type / use_tt     │ bdev (*ttm_device)           │       ║
║  │  │ size (u64)            │ lru (list_heads)             │       ║
║  │  └──────────────────────────────────────────────────────┘       ║
║  │                                                                  ║
║  │  ttm_tt (ttm_tt.h:48)                                          ║
║  │  ┌──────────────────────────────────────────────────────┐       ║
║  │  │ pages (**page)         │ page_flags                  │       ║
║  │  │ num_pages              │ caching (ttm_caching)       │       ║
║  │  │ dma_address (*dma_addr)│ sg (*sg_table)              │       ║
║  │  │ swap_storage (*file)   │ backup (*ttm_backup)        │       ║
║  │  └──────────────────────────────────────────────────────┘       ║
║  │                                                                  ║
║  │  ttm_device (ttm_device.h:215)                                  ║
║  │  ┌──────────────────────────────────────────────────────┐       ║
║  │  │ device_list            │ funcs (*ttm_device_funcs)   │       ║
║  │  │ sysman (res_manager)   │ man_drv[TTM_NUM_MEM_TYPES]  │       ║
║  │  │ vma_manager            │ pool (ttm_pool)             │       ║
║  │  │ lru_lock (spinlock)    │                             │       ║
║  │  └──────────────────────────────────────────────────────┘       ║
║  └──────────────────────────┬─────────────────────────────────────┘ ║
╚════════════════════════════╪════════════════════════════════════════╝
                             │  PCIe BAR / MMIO / IOMMU
╔════════════════════════════╪════════════════════════════════════════╗
║        HARDWARE            ▼                                        ║
║  [ VRAM (HBM/GDDR) ]  [ PCIe BAR ]  [ GART/IOMMU ]  [ GPU MMU ]  ║
╚════════════════════════════════════════════════════════════════════╝
```

---

## 2. Layer-by-layer Component Explanation

### Memory Domains

**Source:** `include/drm/ttm/ttm_placement.h:51-54`

```c
#define TTM_PL_SYSTEM   0   // regular system RAM, not GPU-visible until bound
#define TTM_PL_TT       1   // system RAM mapped through GART/IOMMU for GPU
#define TTM_PL_VRAM     2   // device-local VRAM, fastest for GPU
#define TTM_PL_PRIV     3   // driver-defined memory regions
```

Placement flags (ttm_placement.h:61-65):
```c
#define TTM_PL_FLAG_CONTIGUOUS  (1 << 0)  // must be physically contiguous
#define TTM_PL_FLAG_TOPDOWN     (1 << 1)  // allocate from top of region
#define TTM_PL_FLAG_TEMPORARY   (1 << 2)  // temporary allocation
```

---

### ttm_device (per-GPU TTM instance)

**Source:** `include/drm/ttm/ttm_device.h:215`, `ttm_device.c`

```c
struct ttm_device {
    struct list_head            device_list;     // global TTM device list
    const struct ttm_device_funcs *funcs;        // driver callbacks
    struct ttm_resource_manager  sysman;         // SYSTEM domain manager
    struct ttm_resource_manager *man_drv[TTM_NUM_MEM_TYPES]; // per-type managers
    struct drm_vma_offset_manager *vma_manager;  // mmap offset management
    struct ttm_pool              pool;           // page pool (cached/WC/UC)
    spinlock_t                   lru_lock;       // LRU list protection
    ...
};
```

---

### ttm_device_funcs (driver callback vtable)

**Source:** `include/drm/ttm/ttm_device.h:61`

```c
struct ttm_device_funcs {
    struct ttm_tt *(*ttm_tt_create)(struct ttm_buffer_object *bo, u32 page_flags);
    int (*ttm_tt_populate)(struct ttm_device *bdev, struct ttm_tt *ttm, ...);
    void (*ttm_tt_unpopulate)(struct ttm_device *bdev, struct ttm_tt *ttm);
    void (*ttm_tt_destroy)(struct ttm_device *bdev, struct ttm_tt *ttm);
    bool (*eviction_valuable)(struct ttm_buffer_object *bo, const struct ttm_place *place);
    void (*evict_flags)(struct ttm_buffer_object *bo, struct ttm_placement *placement);
    int (*move)(struct ttm_buffer_object *bo, bool evict, struct ttm_operation_ctx *ctx,
                struct ttm_resource *new_mem, struct ttm_place *hop);
    void (*delete_mem_notify)(struct ttm_buffer_object *bo);
    void (*swap_notify)(struct ttm_buffer_object *bo);
    int (*io_mem_reserve)(struct ttm_device *bdev, struct ttm_resource *mem);
    void (*io_mem_free)(struct ttm_device *bdev, struct ttm_resource *mem);
    unsigned long (*io_mem_pfn)(struct ttm_buffer_object *bo, unsigned long page_offset);
    int (*access_memory)(struct ttm_buffer_object *bo, unsigned long offset, ...);
    void (*release_notify)(struct ttm_buffer_object *bo);
};
```

---

### ttm_buffer_object (BO)

**Source:** `include/drm/ttm/ttm_bo.h:101`

```c
struct ttm_buffer_object {
    struct drm_gem_object    base;          // GEM object (inherited)
    struct ttm_device       *bdev;          // owning TTM device
    enum ttm_bo_type         type;          // DEVICE / KERNEL / SG
    uint32_t                 page_alignment;
    void (*destroy)(struct ttm_buffer_object *); // destructor callback
    struct kref              kref;          // reference count
    struct ttm_resource     *resource;      // current placement
    struct ttm_tt           *ttm;           // backing page array
    bool                     deleted;
    struct ttm_lru_bulk_move *bulk_move;
    unsigned                 priority;
    unsigned                 pin_count;     // pinned → no eviction
    struct work_struct       delayed_delete;
    struct sg_table         *sg;            // for imported DMA-BUF
};
```

---

### ttm_resource (placement descriptor)

**Source:** `include/drm/ttm/ttm_resource.h:253`

```c
struct ttm_resource {
    unsigned long            start;         // region offset
    size_t                   size;          // allocation size
    uint32_t                 mem_type;      // TTM_PL_SYSTEM/TT/VRAM
    uint32_t                 placement;     // placement flags
    struct ttm_bus_placement bus;           // CPU mapping info
    struct ttm_buffer_object *bo;
    struct dmem_cgroup_pool_state *css;
    struct ttm_lru_item      lru;           // for eviction ordering
};
```

---

### ttm_tt (Translation Table / page backing)

**Source:** `include/drm/ttm/ttm_tt.h:48`

```c
struct ttm_tt {
    struct page             **pages;        // page array
    uint32_t                  num_pages;
    uint32_t                  page_flags;   // SWAPPED / ZERO_ALLOC / EXTERNAL / etc.
    enum ttm_caching          caching;      // cached / write_combined / uncached
    struct sg_table          *sg;           // DMA mapping
    dma_addr_t               *dma_address;  // per-page DMA addresses
    struct file              *swap_storage; // swap backing file
    struct ttm_backup        *backup;       // backup storage for swapout
    ...
};
```

Page flags (ttm_tt.h:56-88):
- `TTM_TT_FLAG_SWAPPED` — pages swapped out by TTM
- `TTM_TT_FLAG_ZERO_ALLOC` — zero pages on allocation
- `TTM_TT_FLAG_EXTERNAL` — pages from DMA-BUF or userptr (no TTM swapout)
- `TTM_TT_FLAG_EXTERNAL_MAPPABLE` — external but still mappable by TTM
- `TTM_TT_FLAG_DECRYPTED` — pages marked as not encrypted

---

## 3. Workflow Diagrams

### 3a. Buffer Object Creation

**Source:** `ttm_bo.c:983` (`ttm_bo_init_reserved`, EXPORTED)

```
 Driver                         TTM Core                       Page Pool
    │                              │                               │
    │  ttm_bo_init_reserved()      │                               │
    │  (ttm_bo.c:983)              │                               │
    ├─────────────────────────────►│                               │
    │                              │  kref_init(&bo->kref)         │
    │                              │  bo->bdev = bdev              │
    │                              │  bo->type = type              │
    │                              │                               │
    │                              │  ttm_bo_validate()             │
    │                              │  (ttm_bo.c:893)               │
    │                              ├──────────────────────────────►│
    │                              │  ttm_bo_mem_space()            │
    │                              │  (ttm_bo.c:801)               │
    │                              │  → try placement domains       │
    │                              │  → ttm_resource_alloc()        │
    │                              │                               │
    │                              │  if needs TT (use_tt):        │
    │                              │    funcs->ttm_tt_create()     │
    │                              │    ttm_tt_populate()           │
    │                              │    → ttm_pool_alloc()          │
    │                              │      (ttm_pool.c, EXPORTED)   │
    │                              │    ├──────────────────────────►│
    │                              │    │  alloc pages (cached/WC) │
    │                              │    │◄─── pages[] ─────────────┤
    │                              │                               │
    │◄── ttm_buffer_object ────────┤                               │
```

### 3b. BO Validation & Migration

**Source:** `ttm_bo.c:893` (`ttm_bo_validate`, EXPORTED)

```
 Validator                       TTM Core                     Driver
    │                              │                               │
    │  ttm_bo_validate(bo, place)  │                               │
    ├─────────────────────────────►│                               │
    │                              │  current mem_type != target?  │
    │                              │  YES → need to move           │
    │                              │                               │
    │                              │  ttm_bo_mem_space(new_place)  │
    │                              │  (ttm_bo.c:801)               │
    │                              │  → ttm_resource_alloc()       │
    │                              │                               │
    │                              │  funcs->move(bo, new_res)     │
    │                              │  (driver callback)            │
    │                              ├──────────────────────────────►│
    │                              │  │ DMA copy SYSTEM→VRAM       │
    │                              │  │ or remap GART entries       │
    │                              │  │ set fence on bo             │
    │                              │◄──────────────────────────────┤
    │                              │                               │
    │                              │  old resource freed           │
    │◄── success ──────────────────┤                               │
```

### 3c. BO Eviction (LRU walk)

**Source:** `ttm_bo_util.c:904` (`ttm_lru_walk_for_evict`, EXPORTED)

```
 Allocator                       TTM LRU Manager                  Driver
    │                                 │                              │
    │  ttm_bo_mem_space() fails       │                              │
    │  → no space in VRAM             │                              │
    │                                 │                              │
    │  ttm_lru_walk_for_evict()       │                              │
    │  (ttm_bo_util.c:904)            │                              │
    ├────────────────────────────────►│                              │
    │                                 │  walk LRU list               │
    │                                 │  funcs->eviction_valuable()  │
    │                                 │  funcs->evict_flags()        │
    │                                 │  → get fallback placement    │
    │                                 │                              │
    │                                 │  ttm_bo_validate(victim,     │
    │                                 │                  fallback)   │
    │                                 │  → funcs->move(VRAM→SYSTEM) │
    │                                 ├─────────────────────────────►│
    │                                 │◄─── done ───────────────────┤
    │                                 │                              │
    │◄── retry alloc succeeds ────────┤                              │
```

---

## 4. TTM Page Pool

**Source:** `ttm_pool.c`, `include/drm/ttm/ttm_pool.h:71`

```c
struct ttm_pool {
    struct device *dev;
    bool use_dma_alloc;     // use DMA API or alloc_pages
    bool use_dma32;         // restrict to 32-bit DMA
    struct ttm_pool_type caching[3][NR_PAGE_ORDERS]; // [WC/UC/cached][order]
};
```

```
                    ttm_pool
           ┌───────────┬───────────┐
           │  Cached   │    WC     │  Uncached
           ├───────────┼───────────┤
           │  order-0  │  order-0  │  order-0
           │  order-1  │  order-1  │  order-1
           │  ...      │  ...      │  ...
           │  order-MAX│  order-MAX│  order-MAX
           └───────────┴───────────┘

ttm_pool_alloc() → try pool first → fallback alloc_pages()
ttm_pool_free()  → return to pool → or release to buddy allocator
```

---

## 5. Key Source Files

**All paths verified with `ls` under `~/canonical/kernel/noble-linux-oem/`**

| File | Purpose |
|---|---|
| `drivers/gpu/drm/ttm/ttm_bo.c` | BO lifecycle: init_reserved (L983), validate (L893), mem_space (L801), pin/unpin |
| `drivers/gpu/drm/ttm/ttm_bo_util.c` | BO helpers: move_memcpy (L203), move_accel_cleanup (L708), lru_walk_for_evict (L904), shrink (L1119) |
| `drivers/gpu/drm/ttm/ttm_bo_vm.c` | BO mmap fault handler: vm_fault_reserved (L283) |
| `drivers/gpu/drm/ttm/ttm_resource.c` | Resource alloc/free, LRU management |
| `drivers/gpu/drm/ttm/ttm_pool.c` | Page pool: alloc/free with caching-aware recycling |
| `drivers/gpu/drm/ttm/ttm_tt.c` | Translation table: populate/unpopulate pages |
| `drivers/gpu/drm/ttm/ttm_device.c` | Per-GPU TTM device init/fini |
| `drivers/gpu/drm/ttm/ttm_range_manager.c` | Simple range-based resource allocator |
| `drivers/gpu/drm/ttm/ttm_sys_manager.c` | System memory domain manager |
| `drivers/gpu/drm/ttm/ttm_backup.c` | Swap backup storage for TTM pages |
| `drivers/gpu/drm/ttm/ttm_execbuf_util.c` | Multi-BO reservation for execbufs |

Headers in `include/drm/ttm/`:
| Header | Key Contents |
|---|---|
| `ttm_bo.h` | `ttm_buffer_object` (L101), `ttm_bo_kmap_obj`, `ttm_lru_walk` |
| `ttm_resource.h` | `ttm_resource` (L253), `ttm_resource_manager` (L189), `ttm_lru_item` |
| `ttm_device.h` | `ttm_device` (L215), `ttm_device_funcs` (L61) |
| `ttm_tt.h` | `ttm_tt` (L48), page flags |
| `ttm_placement.h` | `TTM_PL_*` constants, `ttm_place`, `ttm_placement` |
| `ttm_pool.h` | `ttm_pool` (L71), `ttm_pool_type` (L51) |

---

## References

- `drivers/gpu/drm/ttm/ttm_bo.c` — `ttm_bo_init_reserved` (L983), `ttm_bo_validate` (L893)
- `drivers/gpu/drm/ttm/ttm_bo_util.c` — `ttm_bo_move_memcpy` (L203), `ttm_lru_walk_for_evict` (L904)
- `drivers/gpu/drm/ttm/ttm_pool.c` — `ttm_pool_alloc`, `ttm_pool_free`
- `include/drm/ttm/ttm_bo.h` — `ttm_buffer_object` (L101)
- `include/drm/ttm/ttm_placement.h` — `TTM_PL_SYSTEM/TT/VRAM` (L51-54)
