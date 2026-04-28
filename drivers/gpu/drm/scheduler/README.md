# DRM GPU Scheduler (drm_sched) — Deep Dive Analysis

> **Source tree:** `drivers/gpu/drm/scheduler/`
> **Kernel:** noble-linux-oem
> **Date:** 2026-04-28
> **Scanned from:** ~/canonical/kernel/noble-linux-oem

---

## 1. Full Subsystem Stack

```
╔══════════════════════════════════════════════════════════════════════╗
║                        USER SPACE                                    ║
║  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────────────────┐  ║
║  │  Vulkan  │  │  OpenGL  │  │ Compute  │  │  Video Decode/Enc  │  ║
║  │  (Mesa)  │  │  (Mesa)  │  │ (OpenCL) │  │  (VA-API)          │  ║
║  └────┬─────┘  └────┬─────┘  └────┬─────┘  └───────┬────────────┘  ║
║       └─────────────┴──────┬───────┴─────────────────┘              ║
║                            │  libdrm  (submit ioctl)                 ║
╚════════════════════════════╪════════════════════════════════════════╝
                             │  ioctl (EXECBUF / submit / vm_bind)
╔════════════════════════════╪════════════════════════════════════════╗
║         KERNEL — DRM Driver (amdgpu / xe / nouveau / panfrost)      ║
║  ┌─────────────────────────▼──────────────────────────────────────┐ ║
║  │            Driver Submit Path                                   │ ║
║  │  parse cmds → validate → drm_sched_job_init()                  │ ║
║  │  → drm_sched_job_arm() → drm_sched_entity_push_job()          │ ║
║  └──────────────────────────┬─────────────────────────────────────┘ ║
║                             │                                       ║
║  ┌──────────────────────────▼─────────────────────────────────────┐ ║
║  │           DRM GPU SCHEDULER  (gpu_scheduler.h)                  ║
║  │                                                                  ║
║  │  drm_sched_entity          drm_sched_rq                         ║
║  │  ┌──────────────────┐      ┌──────────────────────────────┐    ║
║  │  │ list (rq link)   │      │ sched (owner)                │    ║
║  │  │ lock (spinlock)  │      │ lock (spinlock)              │    ║
║  │  │ rq (current rq)  │─────►│ current_entity (rr pointer)  │    ║
║  │  │ sched_list[]     │      │ entities (list_head)         │    ║
║  │  │ num_sched_list   │      │ rb_tree_root (fifo ordering) │    ║
║  │  │ priority         │      └──────────────────────────────┘    ║
║  │  │ job_queue (spsc) │                                          ║
║  │  │ last_user (task) │      drm_sched_fence                     ║
║  │  │ entity_idle      │      ┌──────────────────────────────┐    ║
║  │  │ dependency       │      │ scheduled (dma_fence)        │    ║
║  │  └──────────────────┘      │ finished  (dma_fence)        │    ║
║  │                            │ deadline  (ktime_t)          │    ║
║  │  drm_sched_job             │ parent    (*dma_fence)       │    ║
║  │  ┌──────────────────┐      │ sched     (*drm_gpu_sched)   │    ║
║  │  │ submit_ts        │      └──────────────────────────────┘    ║
║  │  │ sched (owner)    │                                          ║
║  │  │ s_fence          │──────► drm_sched_fence                   ║
║  │  │ entity           │                                          ║
║  │  │ s_priority       │      drm_gpu_scheduler                   ║
║  │  │ credits (u32)    │      ┌──────────────────────────────┐    ║
║  │  │ karma (atomic)   │      │ ops (*drm_sched_backend_ops) │    ║
║  │  │ queue_node (spsc)│      │ credit_limit / credit_count  │    ║
║  │  │ list (pending)   │      │ timeout (jiffies)            │    ║
║  │  │ dependencies(xa) │      │ num_rqs / **sched_rq         │    ║
║  │  │ finish_cb / work │      │ job_scheduled (waitqueue)    │    ║
║  │  └──────────────────┘      │ work_run_job (work_struct)   │    ║
║  │                            │ work_free_job (work_struct)  │    ║
║  │                            │ work_tdr (delayed_work)      │    ║
║  │                            │ pending_list / job_list_lock │    ║
║  │                            │ ready / pause_submit         │    ║
║  │                            └──────────────────────────────┘    ║
║  └──────────────────────────┬─────────────────────────────────────┘ ║
║                             │  driver callbacks (ops->run_job)      ║
╚════════════════════════════╪════════════════════════════════════════╝
                             │  MMIO / doorbell ring / DMA
╔════════════════════════════╪════════════════════════════════════════╗
║        HARDWARE            ▼                                        ║
║  [ Command Processor ]  [ Execution Units ]  [ Fence Register ]    ║
╚════════════════════════════════════════════════════════════════════╝
```

---

## 2. Layer-by-layer Component Explanation

### Layer 0 — Hardware

| Component | Role |
|---|---|
| Command Processor | Reads command buffers from ring / queue |
| Execution Units | Shader cores, fixed-function units |
| Fence Register | HW writes seqno on command completion; CPU polls or gets IRQ |

---

### Layer 1 — drm_gpu_scheduler (scheduler instance)

**Source:** `include/drm/gpu_scheduler.h:573`, `sched_main.c:1320`

Each GPU engine (gfx ring, compute ring, DMA ring) gets one scheduler instance.
Created by the driver via `drm_sched_init()` (sched_main.c:1320).

**Key difference from older kernels:** This version uses **workqueue-based
scheduling** (`work_run_job`, `work_free_job`) instead of a dedicated kthread.
There is no `drm_sched_main()` function — job dispatch happens via
`drm_sched_run_job_work()` (sched_main.c:1239, static).

```c
// include/drm/gpu_scheduler.h:573
struct drm_gpu_scheduler {
    const struct drm_sched_backend_ops *ops;
    u32                     credit_limit;
    atomic_t                credit_count;
    long                    timeout;
    const char             *name;
    u32                     num_rqs;
    struct drm_sched_rq   **sched_rq;
    wait_queue_head_t       job_scheduled;
    atomic64_t              job_id_count;
    struct workqueue_struct *submit_wq;
    struct workqueue_struct *timeout_wq;
    struct work_struct      work_run_job;   // dispatches jobs to HW
    struct work_struct      work_free_job;  // cleans up finished jobs
    struct delayed_work     work_tdr;       // Timeout Detection & Recovery
    struct list_head        pending_list;   // submitted, awaiting completion
    spinlock_t              job_list_lock;
    int                     hang_limit;
    atomic_t               *score;
    atomic_t                _score;
    bool                    ready;
    bool                    free_guilty;
    bool                    pause_submit;
    bool                    own_submit_wq;
    struct device          *dev;
};
```

Initialization via `drm_sched_init_args` (gpu_scheduler.h:617):

```c
struct drm_sched_init_args {
    const struct drm_sched_backend_ops *ops;
    struct workqueue_struct *submit_wq;   // NULL → allocate ordered wq
    struct workqueue_struct *timeout_wq;  // NULL → system_wq
    u32 num_rqs;                          // ≤ DRM_SCHED_PRIORITY_COUNT
    u32 credit_limit;
    unsigned int hang_limit;              // DEPRECATED, set to 0
    long timeout;                         // jiffies
    atomic_t *score;
    const char *name;
    struct device *dev;
};
```

---

### Layer 2 — drm_sched_rq (run queue)

**Source:** `include/drm/gpu_scheduler.h:251`

One per priority level, dynamically allocated in `drm_sched_init()`:

```c
struct drm_sched_rq {
    struct drm_gpu_scheduler   *sched;
    spinlock_t                  lock;
    struct drm_sched_entity    *current_entity;  // round-robin pointer
    struct list_head            entities;         // entity list (RR mode)
    struct rb_root_cached       rb_tree_root;     // red-black tree (FIFO mode)
};
```

Scheduling policy is controlled by `drm_sched_policy` module parameter
(sched_internal.h):
- `DRM_SCHED_POLICY_RR` (0) — round-robin within each priority level
- `DRM_SCHED_POLICY_FIFO` (1) — earliest-submitted-first using rb_tree

Entity selection (sched_main.c:1116):
```c
for (i = DRM_SCHED_PRIORITY_KERNEL; i < sched->num_rqs; i++) {
    entity = drm_sched_policy == DRM_SCHED_POLICY_FIFO ?
        drm_sched_rq_select_entity_fifo(sched, sched->sched_rq[i]) :
        drm_sched_rq_select_entity_rr(sched, sched->sched_rq[i]);
    if (entity) break;
}
```

---

### Layer 3 — drm_sched_entity (per-context queue)

**Source:** `include/drm/gpu_scheduler.h:82`, `sched_entity.c`

Each userspace context (GL context, Vulkan queue) gets one entity:

```c
struct drm_sched_entity {
    struct list_head            list;          // linked into rq.entities
    spinlock_t                  lock;
    struct drm_sched_rq        *rq;           // current run queue
    struct drm_gpu_scheduler  **sched_list;   // possible schedulers
    unsigned int                num_sched_list;
    enum drm_sched_priority     priority;
    struct spsc_queue           job_queue;     // lock-free SPSC queue
    struct dma_fence           *dependency;    // current dep to wait on
    struct completion           entity_idle;   // signaled when no work
    struct task_struct         *last_user;     // submitting process
    bool                        stopped;
    int                        *guilty;        // set if context caused hang
    ...
};
```

The `job_queue` is a lock-free SPSC (single-producer single-consumer) queue
(sched_internal.h uses `spsc_queue_push/pop/peek`).

Key exported functions (sched_entity.c):
- `drm_sched_entity_init()` — line 116
- `drm_sched_entity_push_job()` — line 631
- `drm_sched_entity_flush()` — line 316
- `drm_sched_entity_destroy()` — line 361
- `drm_sched_entity_set_priority()` — line 393
- `drm_sched_entity_modify_sched()` — line 141

---

### Layer 4 — drm_sched_job (individual GPU job)

**Source:** `include/drm/gpu_scheduler.h:340`

```c
struct drm_sched_job {
    ktime_t                     submit_ts;     // when pushed to entity queue
    struct drm_gpu_scheduler   *sched;         // assigned by drm_sched_job_arm()
    struct drm_sched_fence     *s_fence;       // scheduler-managed fence pair
    struct drm_sched_entity    *entity;
    enum drm_sched_priority     s_priority;
    u32                         credits;       // credit cost of this job
    unsigned int                last_dependency;
    atomic_t                    karma;          // ban threshold counter
    struct spsc_node            queue_node;     // in entity.job_queue
    struct list_head            list;           // in scheduler.pending_list
    union {
        struct dma_fence_cb     finish_cb;
        struct work_struct      work;
    };
    struct dma_fence_cb         cb;
    struct xarray               dependencies;  // dma_fence dependencies
};
```

Key exported functions (sched_main.c):
- `drm_sched_job_init()` — line 857
- `drm_sched_job_arm()` — line 890
- `drm_sched_job_cleanup()` — line 1091
- `drm_sched_job_add_dependency()` — line 936
- `drm_sched_job_add_implicit_dependencies()` — line 1024

---

### Layer 5 — drm_sched_fence (scheduler fence pair)

**Source:** `include/drm/gpu_scheduler.h:264`, `sched_fence.c`

```c
struct drm_sched_fence {
    struct dma_fence        scheduled;   // signaled when job dispatched to HW
    struct dma_fence        finished;    // signaled when HW completes the job
    ktime_t                 deadline;    // propagated to parent fence
    struct dma_fence       *parent;      // HW fence from run_job()
    struct drm_gpu_scheduler *sched;
    spinlock_t              lock;
    void                   *owner;       // for debugging
};
```

Internal functions (sched_fence.c, non-static):
- `drm_sched_fence_alloc()` — allocate fence pair
- `drm_sched_fence_init()` — initialize
- `drm_sched_fence_scheduled()` — signal `scheduled` sub-fence
- `drm_sched_fence_finished()` — signal `finished` sub-fence

---

### Layer 6 — drm_sched_backend_ops (driver implements)

**Source:** `include/drm/gpu_scheduler.h:412`

```c
struct drm_sched_backend_ops {
    struct dma_fence *(*prepare_job)(struct drm_sched_job *sched_job,
                                     struct drm_sched_entity *s_entity);
    struct dma_fence *(*run_job)(struct drm_sched_job *sched_job);
    enum drm_gpu_sched_stat (*timedout_job)(struct drm_sched_job *sched_job);
    void (*free_job)(struct drm_sched_job *sched_job);
};
```

| Callback | When Called | Purpose |
|---|---|---|
| `prepare_job` | Before run_job, in work context | Return extra dma_fence to wait on, or NULL |
| `run_job` | Scheduler dispatches job (work_run_job) | Push command buffer to HW, return HW fence |
| `timedout_job` | TDR fires (work_tdr) | Reset GPU, return RESET or ENODEV |
| `free_job` | After finished fence signals (work_free_job) | Release driver-private resources |

Return values for `timedout_job` (gpu_scheduler.h:399):
```c
enum drm_gpu_sched_stat {
    DRM_GPU_SCHED_STAT_RESET,   // nominal recovery
    DRM_GPU_SCHED_STAT_ENODEV,  // device gone, wedge the scheduler
};
```

---

## 3. Workflow Diagram — Job Lifecycle

**Source:** sched_main.c:1239 (`drm_sched_run_job_work`), sched_entity.c:576 (`drm_sched_entity_push_job`)

```
 Userspace                Driver Submit              Scheduler (workqueue)     Hardware
     │                        │                         │                       │
     │  ioctl(SUBMIT,cmdbuf)  │                         │                       │
     ├───────────────────────►│                         │                       │
     │                        │  drm_sched_job_init()   │                       │
     │                        │  (sched_main.c:857)     │                       │
     │                        │  drm_sched_job_arm()    │                       │
     │                        │  (sched_main.c:890)     │                       │
     │                        │  → alloc s_fence        │                       │
     │                        │  → assign sched + id    │                       │
     │                        │                         │                       │
     │                        │  drm_sched_entity_push_job()                    │
     │                        │  (sched_entity.c:576)   │                       │
     │                        │  → spsc_queue_push()    │                       │
     │                        │  → if first job:        │                       │
     │                        │    drm_sched_wakeup()   │                       │
     │                        │    → queue_work(work_run_job)                   │
     │◄── return s_fence ─────┤                         │                       │
     │                        │                         │                       │
     │                        │              drm_sched_run_job_work():          │
     │                        │              (sched_main.c:1239, static)        │
     │                        │                    ┌────┤                       │
     │                        │                    │ drm_sched_select_entity()  │
     │                        │                    │ (sched_main.c:1116)        │
     │                        │                    │ → scan rq[KERNEL..LOW]     │
     │                        │                    │ → RR or FIFO policy        │
     │                        │                    │                            │
     │                        │                    │ drm_sched_entity_pop_job() │
     │                        │                    │ (sched_entity.c)           │
     │                        │                    │                            │
     │                        │                    │ credit_count += credits    │
     │                        │                    │ drm_sched_job_begin()      │
     │                        │                    │ → add to pending_list      │
     │                        │                    │ → start TDR timer          │
     │                        │                    │                            │
     │                        │                    │ ops->run_job(sched_job)    │
     │                        │                    │    ├──────────────────────►│
     │                        │                    │    │ write ring buffer     │
     │                        │                    │    │ ring doorbell         │
     │                        │                    │    │◄── hw_fence ──────────┤
     │                        │                    │                            │
     │                        │                    │ drm_sched_fence_scheduled()│
     │                        │                    │ (sched_fence.c)            │
     │                        │                    │ → signal s_fence.scheduled │
     │                        │                    │ → set s_fence.parent = hw  │
     │                        │                    │                            │
     │                        │                    │ dma_fence_add_callback(    │
     │                        │                    │   hw_fence, job_done_cb)   │
     │                        │                    │                            │
     │                        │                    │ queue_work(work_run_job)   │
     │                        │                    │ → pick up next job         │
     │                        │                    └────┤                       │
     │                        │                         │                       │
     │                        │                         │    (GPU executes)     │
     │                        │                         │                       │
     │                        │                         │◄── IRQ: hw_fence ─────┤
     │                        │                         │ drm_sched_job_done()   │
     │                        │                         │ (sched_main.c:388)     │
     │                        │                         │ → credit_count -= N   │
     │                        │                         │ → score--              │
     │                        │                         │ → drm_sched_fence_finished()
     │                        │                         │ → queue work_free_job  │
     │                        │                         │                       │
     │                        │              drm_sched_free_job_work():         │
     │                        │              (sched_main.c:1220, static)        │
     │                        │                         │                       │
     │                        │                         │ ops->free_job(job)    │
     │                        │                         │ → driver cleanup      │
     │                        │                         │                       │
     │  poll(fence_fd) → READY│                         │                       │
     ├───────────────────────►│                         │                       │
     │◄── POLLIN ─────────────┤                         │                       │
```

---

## 4. Timeout Detection & Recovery (TDR)

**Source:** sched_main.c:557 (`drm_sched_job_timedout`, static)

```
 work_run_job                    TDR delayed_work                 Driver
      │                               │                               │
      │  job dispatched               │                               │
      │  schedule_delayed_work(tdr)   │                               │
      │  (in drm_sched_job_begin,     │                               │
      │   sched_main.c:521)           │                               │
      ├──────────────────────────────►│ (starts countdown)            │
      │                               │                               │
      │    ... timeout elapses ...    │                               │
      │                               │ drm_sched_job_timedout()      │
      │                               │ (sched_main.c:557, static)    │
      │                               │                               │
      │                               │  list_del_init(job)           │
      │                               │  ops->timedout_job(job)       │
      │                               ├──────────────────────────────►│
      │                               │                               │
      │                               │  ┌─ GPU reset ─────────────┐ │
      │                               │  │ drm_sched_stop()        │ │
      │                               │  │ reset HW                │ │
      │                               │  │ drm_sched_start()       │ │
      │                               │  └─────────────────────────┘ │
      │                               │                               │
      │                               │◄── DRM_GPU_SCHED_STAT_RESET ─┤
      │                               │    (or STAT_ENODEV → wedge)  │
      │  scheduler resumes            │                               │
      │◄──────────────────────────────┤                               │
```

---

## 5. Priority Levels and Scheduling Policy

**Source:** `include/drm/gpu_scheduler.h:65`, `sched_internal.h`

```
DRM_SCHED_PRIORITY_KERNEL (0)  ──►  sched_rq[0]  ◄── kernel recovery jobs
DRM_SCHED_PRIORITY_HIGH   (1)  ──►  sched_rq[1]  ◄── high-priority user ctx
DRM_SCHED_PRIORITY_NORMAL (2)  ──►  sched_rq[2]  ◄── default user contexts
DRM_SCHED_PRIORITY_LOW    (3)  ──►  sched_rq[3]  ◄── background / low-prio

DRM_SCHED_PRIORITY_COUNT  (4)  max number of rqs

drm_sched_policy (module param, sched_internal.h):
  0 = DRM_SCHED_POLICY_RR   → round-robin via entities list + current_entity
  1 = DRM_SCHED_POLICY_FIFO → earliest submit_ts first via rb_tree_root
```

Selection algorithm (sched_main.c:1116, `drm_sched_select_entity`):
```
for prio in [KERNEL → LOW]:
    if policy == FIFO:
        entity = drm_sched_rq_select_entity_fifo(rq[prio])
    else:
        entity = drm_sched_rq_select_entity_rr(rq[prio])
    if entity found: return entity
→ strict priority across levels; RR or FIFO within each level
```

---

## 6. Credit-Based Flow Control

**Source:** sched_main.c:1264, gpu_scheduler.h:575

Unlike older versions that used `hw_submission_limit` (job count), this version
uses **credits**:

```
credit_limit  — max total credits in-flight (set at init)
credit_count  — current credits consumed (atomic_t)

On dispatch:  atomic_add(job->credits, &sched->credit_count)
On complete:  atomic_sub(job->credits, &sched->credit_count)

Run queue check (drm_sched_can_queue):
  if credit_count + job.credits > credit_limit → wait
```

This allows heterogeneous job sizes (e.g., a large compute job uses more credits
than a small blit).

---

## 7. Key Source Files

| File | Lines | Purpose |
|---|---|---|
| `sched_main.c` | ~1540 | Scheduler init/fini, work functions (run_job, free_job, TDR), job lifecycle |
| `sched_entity.c` | ~640 | Entity init/destroy, push_job, pop_job, dependency handling |
| `sched_fence.c` | ~210 | drm_sched_fence alloc/init/signal (scheduled + finished) |
| `sched_internal.h` | ~90 | Internal API: rq add/remove entity, policy constants, SPSC helpers |
| `gpu_scheduler_trace.h` | ~50 | Tracepoint definitions (drm_sched_job_run, drm_sched_job_done, etc.) |

Header: `include/drm/gpu_scheduler.h` (~650 lines) — all public structs & API.

---

## References

- `drivers/gpu/drm/scheduler/sched_main.c` — `drm_sched_init` (L1320), `drm_sched_run_job_work` (L1239)
- `drivers/gpu/drm/scheduler/sched_entity.c` — `drm_sched_entity_push_job` (L576)
- `drivers/gpu/drm/scheduler/sched_fence.c` — `drm_sched_fence_scheduled`, `drm_sched_fence_finished`
- `drivers/gpu/drm/scheduler/sched_internal.h` — `DRM_SCHED_POLICY_RR/FIFO`, `spsc_queue` helpers
- `include/drm/gpu_scheduler.h` — all data structures
