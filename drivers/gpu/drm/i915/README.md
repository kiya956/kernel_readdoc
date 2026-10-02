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

---

## i915 Frontbuffer Tracking: Full Path Diagram for `ORIGIN_CS`, `ORIGIN_FLIP`, `ORIGIN_DIRTYFB`

**Source tree:** `/home/an/canonical/kernel/linux`
**Version:** `VERSION = 7 / PATCHLEVEL = 2 / EXTRAVERSION = -rc1` (`Makefile:1-5`)
**Tree HEAD at time of writing:** `4a50a141f05a` ("Merge tag 'bootconfig-fixes-v7.2-rc1' ...")

> All line numbers below refer to this exact tree. Every claim in this document was read
> out of the local source; no upstream/mailing-list knowledge is assumed.

---

### 1. The common core: what every origin funnels into

All three paths converge on a tiny set of functions in
`drivers/gpu/drm/i915/display/intel_frontbuffer.c`.

#### 1.1 Per-FB state

`intel_frontbuffer.h:43-47`

```c
struct intel_frontbuffer {
        struct intel_display *display;
        atomic_t bits;                 /* which plane slots this FB occupies */
        struct work_struct flush_work; /* used ONLY by the ORIGIN_DIRTYFB path */
};
```

`bits` is maintained by `intel_frontbuffer_track()`
(`intel_frontbuffer.c:214-241`), called from
`intel_atomic_track_fbs()` (`intel_display.c:7662-7673`), from the legacy
cursor path (`intel_cursor.c:892-894`), and from the legacy overlay via
`i915_gem_object_frontbuffer_track()` (`i915_overlay.c:148-149` →
`gem/i915_gem_object_frontbuffer.h:55`).

Note what `bits` means: per `intel_frontbuffer.h:51-53`, a set bit means the BO "is
considered to be the frontbuffer for the given plane interface-wise" and "doesn't mean
that the hw necessarily already scans it out". The bits move during
`intel_atomic_swap_state()` (`intel_display.c:7702`), i.e. *before* commit tail runs.

#### 1.2 Global delayed-flush state

`drivers/gpu/drm/i915/display/intel_display_core.h:150-158`

```c
struct intel_frontbuffer_tracking {
        spinlock_t lock;      /* protects busy_bits */
        unsigned busy_bits;   /* delayed flushing due to gpu activity */
};
```

Instantiated as `display->fb_tracking` (`intel_display_core.h:638`).
**`busy_bits` is set only by `ORIGIN_CS` invalidate and cleared only by
`ORIGIN_CS` flush or by `intel_frontbuffer_flip()`.** It is one shared
frontbuffer-notification coupling point between the three paths (see §6), but
the paths also share reservation and plane fences. Those fences control when
rendering, dirty-FB flushing, and scanout may proceed; they do not impose a
global order among the paths.

#### 1.3 The three core entry functions

| Function | File:line | Called with origin |
| --- | --- | --- |
| `frontbuffer_flush()` (static) | `intel_frontbuffer.c:83-102` | all flush origins |
| `__intel_frontbuffer_invalidate()` | `intel_frontbuffer.c:126-144` | all invalidate origins |
| `__intel_frontbuffer_flush()` | `intel_frontbuffer.c:146-165` | all flush origins |
| `intel_frontbuffer_flip()` | `intel_frontbuffer.c:115-124` | hardcodes `ORIGIN_FLIP` |

Fan-out to the power-saving features (`intel_frontbuffer.c:95-101` and `:138-143`):

```
frontbuffer_flush()                       __intel_frontbuffer_invalidate()
  ├─ trace_intel_frontbuffer_flush()        ├─ trace_intel_frontbuffer_invalidate()
  ├─ might_sleep()                          ├─ might_sleep()
  ├─ intel_td_flush(display)                ├─ intel_psr_invalidate(.., origin)
  ├─ intel_drrs_flush(display, bits)        ├─ intel_drrs_invalidate(.., bits)
  ├─ intel_psr_flush(.., origin)            └─ intel_fbc_invalidate(.., origin)
  └─ intel_fbc_flush(.., origin)
```

Tracepoints: `intel_display_trace.h:809` (`intel_frontbuffer_invalidate`) and
`:830` (`intel_frontbuffer_flush`).

> **`might_sleep()` is load-bearing.** `intel_frontbuffer.c:97` and `:140` both call
> `might_sleep()`. This is why the dirty-FB path (§4) cannot flush directly from a
> dma-fence callback and must bounce through `front->flush_work`.

#### 1.4 Consumer behaviour per origin

**FBC** — `intel_fbc.c`:
* `__intel_fbc_invalidate()` `:1930-1948` → **returns immediately** for
  `ORIGIN_FLIP` and `ORIGIN_CURSOR_UPDATE` (`:1934-1935`). Otherwise sets
  `fbc->busy_bits` and calls `intel_fbc_deactivate(fbc, "frontbuffer write")` (`:1943-1944`).
* `__intel_fbc_flush()` `:1962-1987` → always clears `fbc->busy_bits` (`:1972`), then
  **returns early** for `ORIGIN_FLIP`/`ORIGIN_CURSOR_UPDATE` (`:1974-1975`); otherwise,
  if nothing is busy and no flip pending, `intel_fbc_nuke()` (`:758`) if active, else
  `intel_fbc_activate()` (`:770`).

**PSR** — `intel_psr.c`:
* `intel_psr_invalidate()` `:3634-3661` → **`if (origin == ORIGIN_FLIP) return;`**
  (`:3639-3640`). Otherwise ORs `busy_frontbuffer_bits` and calls
  `_psr_invalidate_handle()` (`:3605`).
* `intel_psr_flush()` `:3746-3788` → clears `busy_frontbuffer_bits`; for `ORIGIN_FLIP`
  (and `ORIGIN_CURSOR_UPDATE` without sel-fetch) it only does
  `tgl_dc3co_flush_locked()` (`:3669`) and returns (`:3773-3777`); otherwise
  `_psr_flush_handle()` (`:3691`).

**DRRS** — `intel_drrs.c`: `intel_drrs_invalidate()` `:272-276` and
`intel_drrs_flush()` `:290-294` both call `intel_drrs_frontbuffer_update()`
`:224-260`, which is **origin-agnostic** — it only cares about busy/idle. It
forces `DRRS_REFRESH_RATE_HIGH` (`:247`) and either schedules the downclock work
(`:254`) or cancels it (`:256`).

**TDF** — `intel_td_flush()` (`intel_tdf.h:20/22`) is called on the flush side only
(`intel_frontbuffer.c:98`); note it is a **no-op for i915** (`intel_tdf.h:19-20`:
`#ifdef I915 ... static inline void intel_td_flush(struct intel_display *display) {}`).

---

### 2. Path A — `ORIGIN_FLIP` (atomic commit)

#### 2.1 Where it actually comes from

`ORIGIN_FLIP` is **never passed as a parameter by a caller**. It is hardcoded inside
`intel_frontbuffer_flip()`:

`intel_frontbuffer.c:115-124`

```c
void intel_frontbuffer_flip(struct intel_display *display,
                            unsigned frontbuffer_bits)
{
        spin_lock(&display->fb_tracking.lock);
        /* Remove stale busy bits due to the old buffer. */
        display->fb_tracking.busy_bits &= ~frontbuffer_bits;   /* :120 */
        spin_unlock(&display->fb_tracking.lock);

        frontbuffer_flush(display, frontbuffer_bits, ORIGIN_FLIP); /* :123 */
}
```

Exactly three call sites exist in the tree:

| Call site | File:line | Bits used |
| --- | --- | --- |
| `intel_post_plane_update()` | `intel_display.c:1047` | `new_crtc_state->fb_bits` |
| `intel_crtc_disable_planes()` | `intel_display.c:1312` | locally accumulated `fb_bits` |
| `i915_overlay_release_old_vma()` | `i915_overlay.c:208` | `overlay->frontbuffer_bits` |

`fb_bits` is reset in `intel_crtc_duplicate_state()`
(`intel_atomic.c:274`: `crtc_state->fb_bits = 0;`) and OR'ed during plane check in
`intel_plane_atomic_check_with_state()` →
`intel_plane.c:723-724`:

```c
if (visible || was_visible)
        new_crtc_state->fb_bits |= plane->frontbuffer_bit;
```

So `fb_bits` is **not** "planes whose pixel content changed": the test at
`intel_plane.c:723` is purely `if (visible || was_visible)`. It means "planes of this
CRTC that are in this transaction and were visible, are visible, or both" — a plane
pulled into the commit without any content change still contributes its bit.

#### 2.2 Commit-work queuing

`intel_atomic_commit()` — `intel_display.c:7707-7775`; wired in as
`.atomic_commit` at `intel_display_driver.c:104`.

`intel_display.c:7761-7773`:

```c
drm_atomic_commit_get(&state->base);                               /* :7761 */
INIT_WORK(&state->base.commit_work, intel_atomic_commit_work);     /* :7762 */

if (nonblock && state->modeset) {
        queue_work(display->wq.modeset, &state->base.commit_work); /* :7765 */
} else if (nonblock) {
        queue_work(display->wq.flip,    &state->base.commit_work); /* :7767 */
} else {
        if (state->modeset)
                flush_workqueue(display->wq.modeset);              /* :7770 */
        intel_atomic_commit_tail(state);                           /* :7771 inline */
}
```

Workqueue properties — `intel_display_driver.c:232-249`:

```c
display->wq.modeset = alloc_ordered_workqueue("i915_modeset", 0);        /* :232 */
display->wq.flip    = alloc_workqueue("i915_flip",
                        WQ_HIGHPRI | WQ_UNBOUND, WQ_UNBOUND_MAX_ACTIVE); /* :238 */
display->wq.cleanup = alloc_workqueue("i915_cleanup",
                        WQ_HIGHPRI | WQ_PERCPU, 0);                      /* :245 */
```

`intel_atomic_commit_work()` (`intel_display.c:7654-7660`) is a thin wrapper that
just calls `intel_atomic_commit_tail(state)`.

#### 2.3 Where in commit tail the flip-flush happens

`intel_atomic_commit_tail()` — `intel_display.c:7423-7652`. Ordered excerpt:

| Step | File:line |
| --- | --- |
| `intel_atomic_commit_fence_wait(state)` | `:7435` |
| `intel_td_flush(display)` | `:7437` |
| `drm_atomic_helper_wait_for_dependencies()` | `:7447` |
| power-domain `DC_OFF` get | `:7478` |
| `commit_modeset_disables` / `commit_modeset_enables` | `:7486`, `:7537` |
| `intel_wait_for_vblank_workers(state)` | `:7542` |
| `drm_atomic_helper_wait_for_flip_done()` | `:7553` |
| optimize watermarks loop | `:7575-7587` |
| **`intel_post_plane_update(state, crtc)`** | **`:7593`** |
| `drm_atomic_helper_commit_hw_done()` | `:7623` |
| `queue_work(display->wq.cleanup, &state->cleanup_work)` | `:7651` |

And `intel_post_plane_update()` (`:1037`) begins with the flip-flush:

`intel_display.c:1045-1052`

```c
enum pipe pipe = crtc->pipe;

intel_frontbuffer_flip(display, new_crtc_state->fb_bits);   /* :1047 */

if (new_crtc_state->update_wm_post && new_crtc_state->hw.active)
        intel_update_watermarks(display);

intel_fbc_post_update(state, crtc);                          /* :1052 */
```

#### 2.4 Correcting the "after-vblank handler" model

`ORIGIN_FLIP` is **not** emitted from a generic vblank IRQ handler or from a vblank
work callback. It is emitted *synchronously inside the commit-tail task*, at
`intel_display.c:7593 → :1047`, i.e. **after** `intel_atomic_commit_tail()` has
already blocked on:

* `intel_wait_for_vblank_workers(state)` — `:7542`, and
* `drm_atomic_helper_wait_for_flip_done()` — `:7553`.

So the *ordering* intuition is normally "after the flip has landed"; the
*mechanism* is not a vblank handler. More precisely, the code has returned
from the flip-done wait helpers: the helper can time out, and a
`legacy_cursor_update` can complete its flip tracking immediately. The
executing context is one of:

1. the `i915_flip` workqueue worker (non-blocking non-modeset commit, `:7767`),
2. the ordered `i915_modeset` workqueue worker (non-blocking modeset, `:7765`), or
3. **the ioctl caller's own task context** (blocking commit, inline `:7771`).

Case 3 means `ORIGIN_FLIP` can be raised directly on a userspace syscall stack —
there is no worker and no vblank callback involved at all. The comment block at
`intel_display.c:7544-7552` explicitly notes this is a FIXME: the code *wants*
to be a vblank worker but currently is not.

Two more consequences:
* `intel_crtc_disable_planes()` (`:1287-1313`) raises `ORIGIN_FLIP` on the *disable*
  side, nowhere near a successful flip — it is called from the modeset-disable
  sequence and flushes the bits of planes that were visible and are being torn down.
* `i915_overlay.c:208` raises `ORIGIN_FLIP` from the legacy overlay old-vma release,
  completely outside the atomic machinery — and, on that path, potentially from a
  dma-fence signal callback: `overlay->last_flip` is initialised with flags `0`
  (`i915_overlay.c:474-475`), so `active_retire()` calls the retire hook inline rather
  than via a worker (`i915_active.c:195-200`) →
  `i915_overlay_last_flip_retire()` (`:232-239`) → `flip_complete()` (`:238`) →
  `i915_overlay_release_old_vid_tail()` (`:215-217`) → `intel_frontbuffer_flip()`
  (`:208`). So the "`ORIGIN_FLIP` always happens in commit tail" rule holds for the
  atomic path only.

#### 2.5 Timeline A

```
USERSPACE                       DRM CORE                    i915 DISPLAY
────────────────────────────────────────────────────────────────────────────────
ioctl(DRM_IOCTL_MODE_ATOMIC)
   └─> drm_mode_atomic_ioctl()           drm_atomic_uapi.c:1601
         └─> drm_atomic_commit()/_nonblocking()  drm_atomic.c:1774 / 1807
               └─> config->funcs->atomic_commit  drm_atomic.c:1789 / 1818
                     = intel_atomic_commit        intel_display_driver.c:104

intel_atomic_commit()                                    intel_display.c:7707
  ├─ intel_atomic_prepare_commit()                                    :7743
  ├─ intel_atomic_setup_commit()                                      :7751
  ├─ intel_atomic_swap_state()                                        :7753
  │     └─ intel_atomic_track_fbs()                                   :7702
  │           └─ intel_frontbuffer_track(old_fb, new_fb, plane_bit)   :7670
  │                 -> atomic_andnot/atomic_or on front->bits   frontbuffer.c:233/239
  ├─ INIT_WORK(commit_work, intel_atomic_commit_work)                 :7762
  └─ dispatch:
        nonblock && modeset  -> queue_work(display->wq.modeset)       :7765
        nonblock             -> queue_work(display->wq.flip)          :7767
        blocking             -> [flush wq.modeset if modeset] then    :7770
                                intel_atomic_commit_tail() INLINE     :7771

                   ... (worker picks up, or inline continues) ...

intel_atomic_commit_tail()                                            :7423
  │ fence wait                                                        :7435
  │ intel_td_flush()                                                  :7437
  │ wait_for_dependencies                                             :7447
  │ DC_OFF power get                                                  :7478
  │ commit_modeset_disables                                           :7486
  │    └─ (modeset-disable route) intel_crtc_disable_planes()         :1287
  │           └─ intel_frontbuffer_flip(display, fb_bits)             :1312  ← ORIGIN_FLIP
  │ commit_modeset_enables                                            :7537
  │ intel_wait_for_vblank_workers()                                   :7542
  │ drm_atomic_helper_wait_for_flip_done()   <-- flip actually landed  :7553
  │ optimize watermarks                                               :7587
  ├─ intel_post_plane_update()                                        :7593 -> :1037
  │     └─ intel_frontbuffer_flip(display, new_crtc_state->fb_bits)   :1047  ← ORIGIN_FLIP
  │           ├─ busy_bits &= ~fb_bits        (stale CS bits dropped) fb.c:120
  │           └─ frontbuffer_flush(.., ORIGIN_FLIP)                   fb.c:123
  │                 ├─ bits &= ~busy_bits  (re-mask vs live GPU work) fb.c:89
  │                 ├─ intel_td_flush()                               fb.c:98
  │                 ├─ intel_drrs_flush()      -> upclock + resched   drrs.c:290
  │                 ├─ intel_psr_flush()       -> DC3CO only, early   psr.c:3773
  │                 └─ intel_fbc_flush()       -> clears busy, early  fbc.c:1974
  │     └─ intel_fbc_post_update()                                    :1052
  │ drm_atomic_helper_commit_hw_done()                                :7623
  └─ queue_work(display->wq.cleanup, cleanup_work)                    :7651
```

---

### 3. Path B — `ORIGIN_CS` (command-stream submission / retire)

This path is an **invalidate-on-submit / flush-on-retire** pair. It is the only origin
that *sets* `display->fb_tracking.busy_bits`; those bits are cleared by the `ORIGIN_CS`
flush (`intel_frontbuffer.c:159`) and by `intel_frontbuffer_flip()`
(`intel_frontbuffer.c:120`).

#### 3.1 Invalidate — at execbuf submission

Entry: `i915_gem_execbuffer2_ioctl()` (`i915_gem_execbuffer.c:3554`) →
`eb_submit()` (`:2418`) → `eb_move_to_gpu()` (`:2080`) →
`_i915_vma_move_to_active()` (`:2135`).

`drivers/gpu/drm/i915/i915_vma.c:2006-2015`

```c
if (flags & EXEC_OBJECT_WRITE) {
        struct i915_frontbuffer *front;

        front = i915_gem_object_frontbuffer_lookup(obj);        /* :2008 */
        if (unlikely(front)) {
                if (intel_frontbuffer_invalidate(&front->base, ORIGIN_CS)) /* :2011 */
                        i915_active_add_request(&front->write, rq);        /* :2012 */
                i915_gem_object_frontbuffer_put(front);
        }
}
```

Key points:
* Only for `EXEC_OBJECT_WRITE` — read-only use of a scanout BO raises nothing.
* `i915_gem_object_frontbuffer_lookup()` (`i915_gem_object_frontbuffer.h:67-92`) is
  RCU-based and returns `NULL` for any BO that is not a frontbuffer — the common case
  costs one `rcu_access_pointer()` test (`:72`).
* `intel_frontbuffer_invalidate()` (`intel_frontbuffer.h:84-98`) returns `false` when
  `atomic_read(&front->bits) == 0` (`:93-94`), i.e. an FB that is not bound to any
  plane never arms the retire hook.
* The `i915_active_add_request()` at `:2012` is what *arms* the flush; it happens only
  if the invalidate was actually performed.

`__intel_frontbuffer_invalidate()` then does the `ORIGIN_CS`-specific bookkeeping:

`intel_frontbuffer.c:132-136`

```c
if (origin == ORIGIN_CS) {
        spin_lock(&display->fb_tracking.lock);
        display->fb_tracking.busy_bits |= frontbuffer_bits;
        spin_unlock(&display->fb_tracking.lock);
}
```

#### 3.2 Flush — at request retire

`front->write` is an `i915_active` initialised in `i915_gem_object_frontbuffer_get()`:

`drivers/gpu/drm/i915/gem/i915_gem_object_frontbuffer.c:47-50`

```c
i915_active_init(&front->write,
                 frontbuffer_active,            /* :9  */
                 frontbuffer_retire,            /* :18 */
                 I915_ACTIVE_RETIRE_SLEEPS);    /* :50 */
```

`frontbuffer_retire()` — `i915_gem_object_frontbuffer.c:18-25`

```c
static void frontbuffer_retire(struct i915_active *ref)
{
        struct i915_frontbuffer *front =
                container_of(ref, typeof(*front), write);

        intel_frontbuffer_flush(&front->base, ORIGIN_CS);   /* :23 */
        i915_gem_object_frontbuffer_put(front);
}
```

The retire is reached through the dma-fence callback machinery in `i915_active.c`:
`node_retire()` (`:219-223`) / `excl_retire()` (`:226-230`) → `active_retire()`
(`:189-201`). Because of `I915_ACTIVE_RETIRE_SLEEPS`:

`i915_active.c:195-198`

```c
if (ref->flags & I915_ACTIVE_RETIRE_SLEEPS) {
        queue_work(system_dfl_wq, &ref->work);
        return;
}
```

→ `active_work()` (`:177-186`) → `__active_retire()` (`:126`) → `ref->retire(ref)`
(`:163-164`) → `frontbuffer_retire()`. The bounce to `system_dfl_wq` is exactly what
satisfies the `might_sleep()` in `frontbuffer_flush()` (`intel_frontbuffer.c:97`),
which `__intel_frontbuffer_flush()` reaches at `:164`.

`__intel_frontbuffer_flush()` then does the `ORIGIN_CS`-specific bookkeeping:

`intel_frontbuffer.c:155-161`

```c
if (origin == ORIGIN_CS) {
        spin_lock(&display->fb_tracking.lock);
        /* Filter out new bits since rendering started. */
        frontbuffer_bits &= display->fb_tracking.busy_bits;
        display->fb_tracking.busy_bits &= ~frontbuffer_bits;
        spin_unlock(&display->fb_tracking.lock);
}
```

Note the asymmetry: the CS flush intersects the FB's *current* bits with the
current display-wide busy mask, and only matching busy bits are cleared and
flushed. This is not a per-BO historical snapshot of the bits present at
invalidate time: a newly acquired bit is filtered only when it is not already
globally busy.

There is a second, origin-generic flush helper,
`__i915_gem_object_frontbuffer_flush()` (`i915_gem_object_frontbuffer.c:107-117`); every
in-tree caller passes `ORIGIN_CPU`, so it does not participate in this path.

Also note that `front->write` is an *aggregate* activity tracker, not a per-request one:
`i915_active_add_request()` keys on the request's timeline and bumps a refcount, and
`active_retire()` bails out while the count is still above one
(`i915_active.c:191-193`). Several overlapping `ORIGIN_CS` invalidations can therefore
be followed by a single `ORIGIN_CS` flush once *all* tracked writes have retired. The
timeline below depicts the single-request case.

#### 3.3 Timeline B

```
USERSPACE / GPU                           i915 GEM                    DISPLAY
──────────────────────────────────────────────────────────────────────────────────
ioctl(DRM_IOCTL_I915_GEM_EXECBUFFER2)
  └─> i915_gem_execbuffer2_ioctl()                    execbuffer.c:3554
        └─> eb_submit()                                           :2418
              └─> eb_move_to_gpu()                                :2080
                    └─> _i915_vma_move_to_active(.., EXEC_OBJECT_WRITE)
                                                       i915_vma.c:1970 (call :2135)
                          ├─ i915_gem_object_frontbuffer_lookup() i915_vma.c:2009
                          ├─ intel_frontbuffer_invalidate(front, ORIGIN_CS)   :2011
                          │     └─ __intel_frontbuffer_invalidate()  frontbuffer.c:126
                          │           ├─ busy_bits |= bits                         :134
                          │           ├─ trace_intel_frontbuffer_invalidate   :138
                          │           ├─ intel_psr_invalidate()  -> PSR exit / CFF
                          │           │                               psr.c:3634
                          │           ├─ intel_drrs_invalidate() -> upclock,
                          │           │     cancel idle work          drrs.c:272
                          │           └─ intel_fbc_invalidate()  -> deactivate
                          │                 "frontbuffer write"        fbc.c:1930
                          └─ i915_active_add_request(&front->write, rq)       :2012

            ===== GPU executes the batch; frontbuffer is "busy" =====
            (any concurrent flush for these bits is suppressed by
             frontbuffer_flush()'s  bits &= ~busy_bits  at frontbuffer.c:89)

GPU completes -> rq fence signals
  └─> node_retire()/excl_retire()                     i915_active.c:219 / 226
        └─> active_retire()                                        :189
              └─> queue_work(system_dfl_wq, &ref->work)  [RETIRE_SLEEPS]  :195-198
                    └─> active_work()                              :177
                          └─> __active_retire()                    :126
                                └─> ref->retire(ref)               :163-164
                                      = frontbuffer_retire()
                                           gem/i915_gem_object_frontbuffer.c:18
                                        └─ intel_frontbuffer_flush(front, ORIGIN_CS) :23
                                             └─ __intel_frontbuffer_flush()
                                                           frontbuffer.c:146
                                                  ├─ bits &= busy_bits;
                                                  │  busy_bits &= ~bits       :158-159
                                                  └─ frontbuffer_flush(ORIGIN_CS)  :164
                                                       ├─ intel_td_flush()         :98
                                                       ├─ intel_drrs_flush()  -> sched
                                                       │     idle downclock  drrs.c:290
                                                       ├─ intel_psr_flush()  -> full
                                                       │     _psr_flush_handle()
                                                       │                   psr.c:3746
                                                       └─ intel_fbc_flush()  -> nuke
                                                             or activate     fbc.c:1989
```

---

### 4. Path C — `ORIGIN_DIRTYFB` (DRM dirty-FB IOCTL)

#### 4.1 Core entry

`drm_mode_dirtyfb_ioctl()` — `drivers/gpu/drm/drm_framebuffer.c:711-777`.
It looks up the FB (`:725`), validates/copies the clip rects (`:737-762`) and then:

`drm_framebuffer.c:764-769`

```c
if (fb->funcs->dirty) {
        ret = fb->funcs->dirty(fb, file_priv, flags, r->color,
                               clips, num_clips);
} else {
        ret = -ENOSYS;
}
```

i915 provides its own `.dirty` (it does **not** use `drm_atomic_helper_dirtyfb`):

`drivers/gpu/drm/i915/display/intel_fb.c:2206-2210`

```c
static const struct drm_framebuffer_funcs intel_fb_funcs = {
        .destroy       = intel_user_framebuffer_destroy,
        .create_handle = intel_user_framebuffer_create_handle,
        .dirty         = intel_user_framebuffer_dirty,     /* :2209 */
};
```

#### 4.2 The i915 `.dirty` callback

`intel_fb.c:2157-2204`

```c
static int intel_user_framebuffer_dirty(struct drm_framebuffer *fb, ...)
{
        struct drm_gem_object *obj = intel_fb_bo(fb);
        struct intel_frontbuffer *front = to_intel_frontbuffer(fb);  /* :2164 */
        ...
        if (!atomic_read(&front->bits))                               /* :2169 */
                return 0;                       /* FB not scanned out: no-op */

        if (dma_resv_test_signaled(obj->resv, dma_resv_usage_rw(false)))
                goto flush;                                           /* :2172-2173 */

        ret = dma_resv_get_singleton(obj->resv, dma_resv_usage_rw(false), &fence);
        if (ret || !fence)
                goto flush;                                           /* :2175-2178 */

        cb = kmalloc_obj(*cb);
        if (!cb) { dma_fence_put(fence); ret = -ENOMEM; goto flush; } /* :2180-2185 */

        cb->front = front;                                            /* :2187 */

        intel_frontbuffer_invalidate(front, ORIGIN_DIRTYFB);          /* :2189 */

        ret = dma_fence_add_callback(fence, &cb->base,
                                     intel_user_framebuffer_fence_wake); /* :2191-2192 */
        if (ret) {
                intel_user_framebuffer_fence_wake(fence, &cb->base);  /* :2194 */
                if (ret == -ENOENT)
                        ret = 0;
        }
        return ret;

flush:
        intel_frontbuffer_flush(front, ORIGIN_DIRTYFB);               /* :2202 */
        return ret;
}
```

The clip rectangles, `flags` and `color` are accepted but not used by i915 — the whole
FB is treated as dirty. `dma_resv_usage_rw(false)` (`include/linux/dma-resv.h:126`) selects
`DMA_RESV_USAGE_WRITE`. Reservation queries include this class and lower
classes, so the query includes outstanding `DMA_RESV_USAGE_KERNEL` and
`DMA_RESV_USAGE_WRITE` fences, but excludes READ and BOOKKEEP fences.
`dma_resv_get_singleton()` can return a fence representing multiple qualifying
fences.

Note the **three distinct outcomes**:
1. **Fast path (`flush:`)** — no outstanding fence (or allocation failure, or
   `dma_resv_get_singleton()` error): flush immediately, *synchronously on the ioctl
   stack*, with no preceding invalidate.
2. **Deferred path** — a fence exists: invalidate now (`:2189`), flush later from
   the fence callback.
3. **`dma_fence_add_callback()` returns non-zero** (fence already signalled → `-ENOENT`):
   the *callback* is invoked manually at `:2194`. Note this does **not** make the flush
   inline — the callback body still goes through `intel_frontbuffer_queue_flush()`
   (`intel_fb.c:2152`) → `schedule_work()` (`intel_frontbuffer.c:189`), so the flush is
   still performed from `front->flush_work`.

#### 4.3 Fence callback → work item

`intel_fb.c:2142-2155`

```c
struct frontbuffer_fence_cb {
        struct dma_fence_cb base;
        struct intel_frontbuffer *front;
};

static void intel_user_framebuffer_fence_wake(struct dma_fence *dma,
                                              struct dma_fence_cb *data)
{
        struct frontbuffer_fence_cb *cb = container_of(data, typeof(*cb), base);

        intel_frontbuffer_queue_flush(cb->front);   /* :2152 */
        kfree(cb);
        dma_fence_put(dma);
}
```

`intel_frontbuffer_queue_flush()` — `intel_frontbuffer.c:183-191`

```c
void intel_frontbuffer_queue_flush(struct intel_frontbuffer *front)
{
        if (!front)
                return;

        intel_parent_frontbuffer_ref(front->display, front);   /* :188 */
        if (!schedule_work(&front->flush_work))                /* :189 */
                intel_parent_frontbuffer_put(front->display, front);
}
```

The ref/put pair (`intel_parent.c:121-129` → `i915_frontbuffer_ref/put` in
`i915_gem_object_frontbuffer.c:143-157`) keeps the frontbuffer alive across the
asynchronous work. If the work was already queued (`schedule_work()` returns
`false`), the extra reference is dropped immediately — the pending work covers it.

`front->flush_work` is initialised once per frontbuffer in
`intel_frontbuffer_init()` (`intel_frontbuffer.c:193-198`,
`INIT_WORK(&front->flush_work, intel_frontbuffer_flush_work)` at `:197`), which the
i915 side calls from `i915_gem_object_frontbuffer_get()`
(`i915_gem_object_frontbuffer.c:41`).

#### 4.4 The work handler

`intel_frontbuffer.c:167-174`

```c
static void intel_frontbuffer_flush_work(struct work_struct *work)
{
        struct intel_frontbuffer *front =
                container_of(work, struct intel_frontbuffer, flush_work);

        intel_frontbuffer_flush(front, ORIGIN_DIRTYFB);               /* :172 */
        intel_parent_frontbuffer_put(front->display, front);          /* :173 */
}
```

#### 4.5 `ORIGIN_DIRTYFB`-specific hook in the flush core

`intel_frontbuffer.c:152-153`

```c
if (origin == ORIGIN_DIRTYFB)
        intel_parent_frontbuffer_flush_for_display(display, front);
```

→ `intel_parent.c:131-134` → `display->parent->frontbuffer->flush_for_display(front)`
→ `i915_frontbuffer_flush_for_display()`
(`i915_gem_object_frontbuffer.c:159-165`) → `i915_gem_object_flush_if_display()`
(`i915_gem_domain.c:103-111`), which takes the object lock and calls
`__i915_gem_object_flush_for_display()`.

**This is unique to `ORIGIN_DIRTYFB`:** it is the only origin for which
`__intel_frontbuffer_flush()` itself invokes the parent flush-for-display hook before
notifying FBC/PSR/DRRS. `ORIGIN_CS` and `ORIGIN_FLIP` do not. (This is not a claim that
no other origin ever flushes BO caches — `ORIGIN_CPU` is raised *after* a clflush in
`gem/i915_gem_clflush.c:23-25` — but there the flushing is done by the caller, not by
`__intel_frontbuffer_flush()`.)

#### 4.6 Timeline C

```
USERSPACE                     DRM CORE                         i915
──────────────────────────────────────────────────────────────────────────────────
ioctl(DRM_IOCTL_MODE_DIRTYFB)
  └─> drm_mode_dirtyfb_ioctl()                      drm_framebuffer.c:711
        ├─ drm_framebuffer_lookup()                                  :725
        ├─ copy_from_user(clips)                                     :756
        └─ fb->funcs->dirty(...)                                     :765
              = intel_user_framebuffer_dirty()           intel_fb.c:2157

 intel_user_framebuffer_dirty()
   ├─ front = to_intel_frontbuffer(fb)                               :2163
   ├─ if (!atomic_read(&front->bits)) return 0;   [not scanned out]  :2169
   │
   ├── CASE 1: dma_resv_test_signaled() == true  -> goto flush       :2172
   │     └─ intel_frontbuffer_flush(front, ORIGIN_DIRTYFB)           :2202
   │           (SYNCHRONOUS, on the ioctl task; no invalidate first)
   │
   ├── CASE 1b: dma_resv_get_singleton() err/NULL, or kmalloc fail
   │     └─ same flush: label at                                     :2201-2202
   │
   └── CASE 2: outstanding fence
         ├─ cb->front = front                                        :2187
         ├─ intel_frontbuffer_invalidate(front, ORIGIN_DIRTYFB)      :2189
         │     └─ __intel_frontbuffer_invalidate()      frontbuffer.c:126
         │           (NOTE: busy_bits NOT touched - not ORIGIN_CS)
         │           ├─ intel_psr_invalidate()   -> PSR exit  psr.c:3634
         │           ├─ intel_drrs_invalidate()  -> upclock  drrs.c:272
         │           └─ intel_fbc_invalidate()   -> deactivate fbc.c:1930
         └─ dma_fence_add_callback(fence, cb, ..fence_wake)          :2191
               │   (if it returns != 0, callback is run inline at    :2194)
               ▼
            ... fence signals (may be IRQ / atomic context) ...
               │
          intel_user_framebuffer_fence_wake()            intel_fb.c:2147
            ├─ intel_frontbuffer_queue_flush(cb->front)              :2152
            │     ├─ intel_parent_frontbuffer_ref()      frontbuffer.c:188
            │     └─ schedule_work(&front->flush_work)               :189
            │           (deferral is REQUIRED: frontbuffer_flush()
            │            calls might_sleep() at frontbuffer.c:97)
            ├─ kfree(cb)                                             :2153
            └─ dma_fence_put(dma)                                    :2154
               │
               ▼ (system workqueue)
          intel_frontbuffer_flush_work()               frontbuffer.c:167
            ├─ intel_frontbuffer_flush(front, ORIGIN_DIRTYFB)        :172
            │     └─ __intel_frontbuffer_flush()                     :146
            │           ├─ intel_parent_frontbuffer_flush_for_display()  :152-153
            │           │     -> i915_gem_object_flush_if_display()
            │           │                          i915_gem_domain.c:103
            │           └─ frontbuffer_flush(.., ORIGIN_DIRTYFB)     :164
            │                 ├─ bits &= ~busy_bits  (CS may defer!) :89
            │                 ├─ intel_td_flush()                    :98
            │                 ├─ intel_drrs_flush()           drrs.c:290
            │                 ├─ intel_psr_flush()  -> _psr_flush_handle()
            │                 │                             psr.c:3746
            │                 └─ intel_fbc_flush()  -> nuke/activate
            │                                              fbc.c:1989
            └─ intel_parent_frontbuffer_put()                        :173
```

---

### 5. Comparison table

| | **A. `ORIGIN_FLIP`** | **B. `ORIGIN_CS`** | **C. `ORIGIN_DIRTYFB`** |
| --- | --- | --- | --- |
| **User entry** | `DRM_IOCTL_MODE_ATOMIC` (`drm_atomic_uapi.c:1601`) / legacy pageflip / overlay ioctl | `DRM_IOCTL_I915_GEM_EXECBUFFER2` (`i915_gem_execbuffer.c:3554`) | `DRM_IOCTL_MODE_DIRTYFB` (`drm_framebuffer.c:711`) |
| **i915 hook** | `.atomic_commit = intel_atomic_commit` (`intel_display_driver.c:104`) | `_i915_vma_move_to_active()` (`i915_vma.c:1970`) | `.dirty = intel_user_framebuffer_dirty` (`intel_fb.c:2209`) |
| **Emits invalidate?** | **No.** `intel_frontbuffer_flip()` only flushes | **Yes**, `i915_vma.c:2011` | **Yes, conditionally** — only when a fence is pending (`intel_fb.c:2189`) |
| **Emits flush?** | Yes — `intel_frontbuffer.c:123` | Yes, on retire — `i915_gem_object_frontbuffer.c:23` | Yes — `intel_fb.c:2202` (sync) or `intel_frontbuffer.c:172` (async) |
| **Function used** | `intel_frontbuffer_flip()` (display-wide bitmask) | `intel_frontbuffer_invalidate()`/`_flush()` (per-FB) | `intel_frontbuffer_invalidate()`/`_flush()` (per-FB) |
| **Bits source** | `crtc_state->fb_bits` (`intel_plane.c:724`) or local mask (`intel_display.c:1309`) | `atomic_read(&front->bits)` (`intel_frontbuffer.h:92`) | `atomic_read(&front->bits)` (`intel_frontbuffer.h:92/120`) |
| **Touches `fb_tracking.busy_bits`?** | **Clears** them (`intel_frontbuffer.c:119`) | **Sets** (`:132-136`) and **clears** (`:155-161`) | No |
| **Can be deferred by `busy_bits`?** | Partly — it clears its own bits first, then `frontbuffer_flush()` re-masks (`:89`) against any bits set after | Mostly no — it clears them at `:159` before calling `frontbuffer_flush()`, but the gate at `:89` is still applied, so a racing new `ORIGIN_CS` invalidate can suppress it too | **Yes** — a DIRTYFB flush for a plane with in-flight GPU writes is dropped at `:89` |
| **Flushes BO cache/domain?** | No | No | **Yes** — `intel_parent_frontbuffer_flush_for_display()` (`:152-153`) |
| **FBC invalidate** | skipped (`fbc.c:1934`) | deactivate (`fbc.c:1943-1944`) | deactivate (`fbc.c:1943-1944`) |
| **FBC flush** | clears busy bits then early-returns (`fbc.c:1972,1974`) | nuke / activate (`fbc.c:1980-1983`) | nuke / activate (`fbc.c:1980-1983`) |
| **PSR invalidate** | early return (`psr.c:3639`) | `_psr_invalidate_handle()` (`psr.c:3605`) | `_psr_invalidate_handle()` (`psr.c:3605`) |
| **PSR flush** | DC3CO only (`psr.c:3773-3777`) | `_psr_flush_handle()` (`psr.c:3691`) | `_psr_flush_handle()` (`psr.c:3691`) |
| **DRRS** | origin-agnostic (`drrs.c:224`); flush → upclock, then schedule idle downclock only if no bits remain busy (`drrs.c:253-254`) | same, plus invalidate → upclock and `cancel_delayed_work()` (`drrs.c:256`) | same |
| **Execution context** | `i915_flip` WQ, ordered `i915_modeset` WQ, **or the ioctl task inline** (`intel_display.c:7765/7767/7771`); the legacy-overlay site can additionally run from a fence-signal callback (`i915_overlay.c:474-475`, `i915_active.c:195-200`) | invalidate: ioctl task; flush: `system_dfl_wq` via `I915_ACTIVE_RETIRE_SLEEPS` (`i915_active.c:195-198`) | invalidate: ioctl task; flush: ioctl task (fast path) **or** `schedule_work()` system WQ (`intel_frontbuffer.c:189`) |
| **Sleepable at flush time?** | Atomic commit: yes; legacy overlay: not guaranteed | Yes (that is why the retire is bounced to a worker) | Yes (that is why the fence cb is bounced to a worker) |
| **Dedicated work item** | `state->base.commit_work` + `state->cleanup_work` | `i915_active.work` (`i915_active.c:196`) | `front->flush_work` (`intel_frontbuffer.h:46`) |

---

### 6. Coexistence and ordering constraints

#### 6.1 All three can be live at the same time

There is no mutual exclusion between the paths. They are independent producers of
invalidate/flush events against the same `display->fb_tracking` and the same
per-feature busy masks (`fbc->busy_bits`, `intel_dp->psr.busy_frontbuffer_bits`,
`crtc->drrs.busy_frontbuffer_bits`). A realistic concurrent scenario:

```
t0  execbuf writes tracked FB-X --------> ORIGIN_CS invalidate  (busy_bits |= X)
t1  userspace DIRTYFB on tracked FB-X --> ORIGIN_DIRTYFB invalidate (no busy_bits)
t2  atomic commit flips FB-X onto plane -> commit_work queued on wq.flip
t3  GPU retire of t0's request ---------> ORIGIN_CS flush (busy_bits &= ~X)
t4  dirtyfb fence cb fires -------------> front->flush_work -> ORIGIN_DIRTYFB flush
t5  commit_tail reaches :7593/:1047 ----> ORIGIN_FLIP flush
```

#### 6.2 Hard ordering rules enforced by the code

1. **An `ORIGIN_CS` flush never precedes its `ORIGIN_CS` invalidate.**
   `i915_active_add_request(&front->write, rq)` (`i915_vma.c:2012`) is executed only
   when the invalidate returned `true` (`:2011`), and the retire runs from the fence
   completion chain. Two caveats from the source: the flush is *aggregate* (it fires
   when the activity count drops, `i915_active.c:191-193`), and the return value of
   `i915_active_add_request()` is ignored at `i915_vma.c:2012`. An acquisition
   failure need not schedule retirement; a later allocation failure releases the
   acquired activity reference and can trigger a CS flush without waiting for this
   request.

2. **`busy_bits` gates every other flush while GPU writes are in flight.**
   `frontbuffer_flush()` (`intel_frontbuffer.c:87-93`):
   ```c
   spin_lock(&display->fb_tracking.lock);
   frontbuffer_bits &= ~display->fb_tracking.busy_bits;
   spin_unlock(&display->fb_tracking.lock);
   if (!frontbuffer_bits)
           return;
   ```
   So in the t1→t4 window above, the `ORIGIN_DIRTYFB` flush for FB-X's bits is
   **silently dropped** if the `ORIGIN_CS` flush (t3) has not yet cleared them.
   The `ORIGIN_CS` retire flush at t3 is then what lets FBC/PSR reconsider
   re-activation — "lets reconsider", not "re-enables": FBC still bails out on
   `fbc->busy_bits || fbc->flip_pending` (`intel_fbc.c:1977-1978`) and PSR still bails
   out while `psr.pause_counter` is non-zero (`intel_psr.c:3770-3771`).
   This is the documented "Flushes will get delayed if they're blocked by some
   outstanding asynchronous rendering" behaviour (`intel_frontbuffer.c:77-79`).

3. **`ORIGIN_FLIP` deliberately breaks rule 2 for its own bits, then re-applies it.**
   `intel_frontbuffer_flip()` clears `busy_bits` for the flipped bits *first*
   (`:118-121`, comment: "Remove stale busy bits due to the old buffer"), because the
   old buffer's pending GPU writes are irrelevant once a new buffer is scanned out.
   It then calls `frontbuffer_flush()`, which re-masks against whatever `busy_bits`
   remain (`:89`) — so a *concurrent* `ORIGIN_CS` invalidate racing in between can
   still defer part of the flip-flush.

4. **The `intel_post_plane_update()` `ORIGIN_FLIP` is ordered after the flip-done
   wait returns.** `intel_post_plane_update()` is called at `intel_display.c:7593`,
   after `intel_wait_for_vblank_workers()` (`:7542`) and
   `drm_atomic_helper_wait_for_flip_done()` (`:7553`), and before
   `drm_atomic_helper_commit_hw_done()` (`:7623`). Two qualifications:
   * the wait helper does not guarantee hardware completion — it times out after 10 s,
     logs `"flip_done timed out"` and continues (`drm_atomic_helper.c:1959-1962`);
   * the **other** `ORIGIN_FLIP` site, `intel_crtc_disable_planes()` (`:1312`), is
     reached from `intel_commit_modeset_disables()` (`:7486`) and therefore runs
     *before* that wait.

5. **Commits are serialised against each other by the workqueues and by the DRM core.**
   * Non-blocking modesets use the *ordered* `wq.modeset` (`intel_display_driver.c:232`)
     → at most one modeset commit tail at a time.
   * Non-blocking flips use the unbound, high-priority `wq.flip`
     (`intel_display_driver.c:238`) → multiple CRTCs' commit tails may run in parallel;
     per-CRTC serialisation comes from
     `drm_atomic_helper_wait_for_dependencies()` (`intel_display.c:7447`).
   * A blocking commit with `state->modeset` first does
     `flush_workqueue(display->wq.modeset)` (`:7770`) before running the tail inline
     (`:7771`).

6. **`front->flush_work` self-coalesces.**
   `schedule_work()` returning `false` (`intel_frontbuffer.c:189`) means a flush is
   already pending for that frontbuffer; the extra reference is dropped and the
   already-queued work covers the new dirty notification. Multiple rapid DIRTYFB
   ioctls therefore collapse into at most one pending async flush per FB.

7. **The dirty-FB fast path can run *before* an invalidate that never happened.**
   In the `goto flush` cases (`intel_fb.c:2172-2185`) there is no matching
   invalidate. This is intentional: `intel_psr_flush()`/`intel_fbc_flush()` are
   documented as "flush = invalidate + flush" (`intel_psr.c:3783`).

#### 6.3 Context constraints summary

| Event | Can it run in atomic/IRQ context? | Why |
| --- | --- | --- |
| `ORIGIN_CS` invalidate | No — runs on the execbuf ioctl task | `might_sleep()` at `intel_frontbuffer.c:140` |
| `ORIGIN_CS` flush | No — bounced to `system_dfl_wq` | `I915_ACTIVE_RETIRE_SLEEPS`, `i915_active.c:195-198` |
| `ORIGIN_DIRTYFB` invalidate | No — runs on the dirtyfb ioctl task | `might_sleep()` at `intel_frontbuffer.c:140` |
| `ORIGIN_DIRTYFB` flush (async) | No — bounced via `schedule_work()` | `might_sleep()` at `intel_frontbuffer.c:97`; dma-fence callbacks may run in IRQ context |
| `ORIGIN_FLIP` flush (atomic commit) | No — commit tail is always a sleepable context | `might_sleep()` at `intel_frontbuffer.c:97`; tail takes mutexes (e.g. `fbc->lock`, `psr.lock`) |
| `ORIGIN_FLIP` flush (legacy overlay) | **Not guaranteed** — `overlay->last_flip` is initialised with flags `0` (`i915_overlay.c:474-475`), so `active_retire()` can run `i915_overlay_last_flip_retire()` directly from the fence-signal callback (`i915_active.c:195-200`) → `flip_complete()` (`:238`) → `i915_overlay_release_old_vid_tail()` (`:215-217`) → `intel_frontbuffer_flip()` (`:208`) | `might_sleep()` is only an assertion; it does not create a sleepable context. Legacy-overlay-capable platforms include i965g/i965gm Gen4. |

---

### 7. Combined master diagram

```
                         ┌──────────────────────────────────────────┐
                         │             U S E R S P A C E            │
                         └──┬──────────────┬───────────────────┬────┘
                            │              │                   │
            DRM_IOCTL_MODE_ │    DRM_IOCTL_I915_GEM_      DRM_IOCTL_MODE_
                 ATOMIC     │       EXECBUFFER2               DIRTYFB
                            │              │                   │
   drm_atomic_uapi.c:1601 ──┤   execbuffer.c:3554 ──┤   drm_framebuffer.c:711 ──┤
   drm_atomic.c:1789/1818   │   eb_submit()  :2418  │   fb->funcs->dirty :765   │
                            ▼                       ▼                           ▼
   intel_atomic_commit()              eb_move_to_gpu() :2080     intel_user_framebuffer_dirty()
     intel_display.c:7707                    │                       intel_fb.c:2157
            │                                ▼                              │
            │                   _i915_vma_move_to_active()                  │
            │                        i915_vma.c:1970           ┌────────────┴────────────┐
            │                                │                 │                         │
            │                                │            fence pending?             no fence
            │                                │                 │                         │
   ┌────────┴────────┐                       │                 ▼                         ▼
   │ nonblock+modeset│                       │     invalidate ORIGIN_DIRTYFB     flush ORIGIN_DIRTYFB
   │  wq.modeset:7765│                       │          intel_fb.c:2189           intel_fb.c:2202
   │ nonblock        │                       │                 │                   (synchronous)
   │  wq.flip   :7767│                       │                 ▼
   │ blocking        │                       │     dma_fence_add_callback :2191
   │  inline    :7771│                       │                 │
   └────────┬────────┘                       │                 ▼  (fence signals)
            ▼                       invalidate ORIGIN_CS   ..._fence_wake() :2147
   intel_atomic_commit_tail()        i915_vma.c:2011            │
       intel_display.c:7423          busy_bits |= bits          ▼
            │                        frontbuffer.c:132  intel_frontbuffer_queue_flush()
            │ wait vblank workers :7542     │                frontbuffer.c:183
            │ wait flip done      :7553     │                      │ schedule_work :189
            │                               │                      ▼
            ▼                     ┌─ GPU executes ─┐     intel_frontbuffer_flush_work()
   intel_post_plane_update()      │                │           frontbuffer.c:167
      intel_display.c:1037        │  rq fence      │                 │
            │                     │  signals       │                 │
            ▼                     └────────┬───────┘                 │
   intel_frontbuffer_flip()                ▼                         │
      frontbuffer.c:115        i915_active.c:189/195 queue_work      │
       busy_bits &= ~bits :119           │                           │
            │                            ▼                           │
            │                  frontbuffer_retire()                  │
            │             gem/i915_gem_object_frontbuffer.c:18        │
            │                            │                           │
            ▼                            ▼                           ▼
   frontbuffer_flush(ORIGIN_   __intel_frontbuffer_flush   __intel_frontbuffer_flush
        FLIP) frontbuffer.c:83        (ORIGIN_CS)              (ORIGIN_DIRTYFB)
            │                     frontbuffer.c:146          frontbuffer.c:146
            │                     busy_bits &= ~bits :159   flush_for_display :153
            │                            │                           │
            └──────────────┬─────────────┴───────────────────────────┘
                           ▼
              frontbuffer_flush()  frontbuffer.c:83
                 bits &= ~busy_bits                 :89   ← global gate
                 ├─ intel_td_flush()                :98
                 ├─ intel_drrs_flush()      drrs.c:290  → upclock + idle timer
                 ├─ intel_psr_flush()        psr.c:3746 → DC3CO (FLIP) | full (CS/DIRTYFB)
                 └─ intel_fbc_flush()        fbc.c:1989 → early-out (FLIP) | nuke/activate
```

---

### 8. Quick reference — file map

| Concern | Path |
| --- | --- |
| Origin enum, inline wrappers | `drivers/gpu/drm/i915/display/intel_frontbuffer.h` |
| Core invalidate/flush/flip/track | `drivers/gpu/drm/i915/display/intel_frontbuffer.c` |
| Atomic commit + `intel_post_plane_update` | `drivers/gpu/drm/i915/display/intel_display.c` |
| Atomic ioctl dispatch | `drivers/gpu/drm/drm_ioctl.c:702` → `drm_atomic_uapi.c:1601` |
| `fb_bits` accumulation | `drivers/gpu/drm/i915/display/intel_plane.c:724`, reset `intel_atomic.c:274` |
| Workqueue creation | `drivers/gpu/drm/i915/display/intel_display_driver.c:226-255` |
| `.dirty` / fence cb | `drivers/gpu/drm/i915/display/intel_fb.c:2142-2210` |
| DRM dirty-FB ioctl | `drivers/gpu/drm/drm_framebuffer.c:711`; dispatch `drivers/gpu/drm/drm_ioctl.c:695` |
| CS invalidate | `drivers/gpu/drm/i915/i915_vma.c:2006-2015` |
| Legacy overlay `ORIGIN_FLIP` | `drivers/gpu/drm/i915/i915_overlay.c:148-149,198-217,232-239,474-475` |
| `ORIGIN_CPU` after clflush (for contrast) | `drivers/gpu/drm/i915/gem/i915_gem_clflush.c:23-25` |
| CS retire/flush | `drivers/gpu/drm/i915/gem/i915_gem_object_frontbuffer.c:18-25` |
| `i915_active` retire mechanics | `drivers/gpu/drm/i915/i915_active.c:126-230` |
| Parent (display↔GEM) indirection | `drivers/gpu/drm/i915/display/intel_parent.c:115-134` |
| FBC hooks | `drivers/gpu/drm/i915/display/intel_fbc.c:1930-1998` |
| PSR hooks | `drivers/gpu/drm/i915/display/intel_psr.c:3605-3800` |
| DRRS hooks | `drivers/gpu/drm/i915/display/intel_drrs.c:224-294` |
| Tracepoints | `drivers/gpu/drm/i915/display/intel_display_trace.h:809,830` |
| Global busy-bit state | `drivers/gpu/drm/i915/display/intel_display_core.h:150-158,638` |
| Subsystem DOC comment | `drivers/gpu/drm/i915/display/intel_frontbuffer.c:27-56` |
