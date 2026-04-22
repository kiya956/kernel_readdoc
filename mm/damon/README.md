# Linux Kernel DAMON — Data Access MONitor

## Overview

**DAMON** (Data Access MONitor) is a Linux kernel framework for **monitoring
actual memory access patterns** at low overhead. It enables smarter
memory management decisions by exposing which memory regions are hot (frequently
accessed) or cold (rarely accessed). DAMON also supports **DAMOS** (DAMON-based
Operation Schemes) — automatic actions triggered by access patterns (e.g.,
swap cold pages, apply MADV_COLD, reclaim idle memory).

Source: `mm/damon/`, `include/linux/damon.h`.

---

## Subsystem Stack

```
┌─────────────────────────────────────────────────────────────────┐
│                        USERSPACE                                │
│                                                                 │
│  /sys/kernel/mm/damon/admin/  (sysfs interface)                │
│  ├── kdamonds/0/state         ← "on" / "off"                   │
│  ├── kdamonds/0/contexts/0/   ← monitoring context             │
│  │   ├── monitoring_attrs/    ← sample / aggr / update interval│
│  │   ├── targets/             ← address ranges to monitor      │
│  │   └── schemes/             ← DAMOS action rules             │
│  │       └── 0/action         ← reclaim / lru_prio / pageout…  │
│                                                                 │
│  damo tool / perf mem / BPF programs                           │
└─────────────────────────────┬───────────────────────────────────┘
                              │ sysfs / procfs
┌─────────────────────────────▼───────────────────────────────────┐
│                     DAMON SYSFS INTERFACE                       │
│      (mm/damon/sysfs.c + sysfs-common.c + sysfs-schemes.c)     │
│                                                                 │
│  Parses user config → creates damon_ctx / damon_target /        │
│  damos structs → calls damon_start(kdamonds)                   │
└─────────────────────────────┬───────────────────────────────────┘
                              │
┌─────────────────────────────▼───────────────────────────────────┐
│                       DAMON CORE                                │
│                    (mm/damon/core.c)                            │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  kdamond  (kernel thread, one per damon_ctx)             │  │
│  │                                                          │  │
│  │  Loop:                                                   │  │
│  │   1. ops->prepare_access_checks()  ← arm traps/PTE bits │  │
│  │   2. sleep(sample_interval)                             │  │
│  │   3. ops->check_accesses()         ← count accesses     │  │
│  │   4. aggregate to damon_region.nr_accesses              │  │
│  │   5. (every aggr_interval) apply DAMOS schemes          │  │
│  │   6. (every update_interval) ops->update()              │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                 │
│  Data model:                                                    │
│  damon_ctx → list of damon_target → list of damon_region        │
│                                     [start, end)  nr_accesses   │
│  damos → action + access_pattern + quota + watermarks           │
└──────────────────────┬──────────────────────────────────────────┘
                       │ damon_operations vtable
        ┌──────────────┴──────────────────────────┐
        │                                         │
┌───────▼─────────────────┐     ┌─────────────────▼────────────┐
│  VADDR ops              │     │  PADDR ops                   │
│  mm/damon/vaddr.c       │     │  mm/damon/paddr.c            │
│                         │     │                              │
│  Virtual address space  │     │  Physical address space      │
│  monitoring.            │     │  monitoring (system-wide).   │
│  Uses PTE Accessed bit  │     │  Uses page_idle_get/set via  │
│  sampling.              │     │  page flags.                 │
│  One damon_ctx per      │     │  Used by system-level        │
│  mm_struct.             │     │  reclaim/proactive reclaim.  │
└─────────────────────────┘     └──────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────────────┐
│              DAMOS ACTION MODULES                               │
│                                                                 │
│  mm/damon/reclaim.c  ── madvise(MADV_COLD) / page reclaim      │
│  mm/damon/lru_sort.c ── LRU list prioritization                │
│  DAMOS built-ins: pageout, lru_prio, lru_deprio,               │
│                   migrate_cold, migrate_hot, stat               │
└─────────────────────────────────────────────────────────────────┘
```

---

## Layer-by-Layer Explanation

### 1. kdamond — Monitoring Thread

Each `damon_ctx` spawns a **kdamond** kernel thread. The thread runs a tight
sampling loop:

1. **Prepare** — call `ops->prepare_access_checks()`: clear Accessed bits on PTEs
   (vaddr) or set page idle flags (paddr) for all monitored regions.
2. **Sleep** for `sample_interval` (default: 5 ms).
3. **Check** — call `ops->check_accesses()`: scan regions, count how many pages
   within each `damon_region` had the Accessed bit set during the interval.
   Accumulate into `region->nr_accesses`.
4. **Aggregate** — every `aggr_interval` (default: 100 ms): apply DAMOS schemes
   to hot/cold regions; reset `nr_accesses`.
5. **Update** — every `update_interval` (default: 1 s): call `ops->update()` to
   discover new VMA ranges (for vaddr).

### 2. damon_region — The Fundamental Unit

```
struct damon_region {
    unsigned long start, end;   // [start, end) address range
    unsigned int  nr_accesses;  // access count in this aggr interval
    unsigned int  age;          // how many intervals without access
};
```

Regions are dynamically split and merged by the adaptive region sizing
mechanism to balance overhead vs. resolution.

### 3. DAMOS — Data Access Monitoring-based Operation Schemes

A **scheme** says: "if a region matches this access pattern, do this action".

```
access_pattern:
  min_sz_region / max_sz_region
  min_nr_accesses / max_nr_accesses   ← hot or cold range
  min_age / max_age                   ← how long in that state

action:
  DAMOS_PAGEOUT     ← reclaim cold pages (swap out)
  DAMOS_LRU_PRIO    ← move hot pages to LRU active list
  DAMOS_LRU_DEPRIO  ← move cold pages to LRU inactive list
  DAMOS_MIGRATE_HOT/COLD ← NUMA-aware hot/cold migration
  DAMOS_STAT        ← collect statistics only (no action)

quota:
  max_sz / time_ms  ← rate-limit the action
  quota goals       ← auto-tune quota to hit target (e.g., 80% DRAM util)

watermarks:
  metric + high/mid/low  ← only activate scheme when metric is in range
  (e.g., only reclaim when free_mem_rate < 50%)
```

### 4. Monitoring Operations (ops)

| Ops ID | File | Mechanism | Scope |
|---|---|---|---|
| `DAMON_OPS_VADDR` | `vaddr.c` | PTE Accessed bit | Per-process virtual address |
| `DAMON_OPS_FVADDR` | `vaddr.c` | PTE Accessed bit, fixed ranges | Per-process (fixed regions) |
| `DAMON_OPS_PADDR` | `paddr.c` | Page idle flag | System-wide physical address |

### 5. Built-in Modules

| Module | File | Purpose |
|---|---|---|
| DAMON_RECLAIM | `mm/damon/reclaim.c` | Proactive LRU-aware cold page reclaim |
| DAMON_LRU_SORT | `mm/damon/lru_sort.c` | Prioritize hot/deprioritize cold on LRU |

Both are built-in kernel features with sysfs knobs under
`/sys/module/damon_reclaim/parameters/` and `/sys/module/damon_lru_sort/parameters/`.

---

## Access Pattern Monitoring Flow

```
kdamond thread              damon_ops (vaddr)          Memory
    │                            │                        │
    │  prepare_access_checks()   │                        │
    │ ──────────────────────────►│  clear PTE Accessed    │
    │                            │  bits in all regions   │
    │  sleep(sample_interval)    │ ─────────────────────►(PTE A=0)
    │                            │                        │
    │                            │                        │ CPU accesses page
    │                            │                        │ MMU sets A=1
    │  check_accesses()          │                        │
    │ ──────────────────────────►│  scan PTEs             │
    │                            │  region.nr_accesses++  │◄─── A=1 found
    │                            │                        │
    │  (every aggr_interval)     │                        │
    │  damos_apply_schemes()     │                        │
    │  ─── if region is cold:    │                        │
    │      DAMOS_PAGEOUT         │                        │
    │      → do_swap_page()      │                        │
    │  ─── if region is hot:     │                        │
    │      DAMOS_LRU_PRIO        │                        │
    │      → lru_cache_add()     │                        │
```

---

## Sysfs Configuration Example

```bash
# Enable DAMON proactive reclaim (kernel module)
echo Y > /sys/module/damon_reclaim/parameters/enabled

# Or manual setup via admin sysfs:
cd /sys/kernel/mm/damon/admin/kdamonds/0
echo on > state

# Check monitored regions (after monitoring starts):
cat contexts/0/targets/0/regions/*/nr_accesses
```

---

## Key Data Structures

| Structure | Purpose |
|---|---|
| `struct damon_ctx` | Monitoring context (holds targets, ops, schemes) |
| `struct damon_target` | One monitoring target (process or address range) |
| `struct damon_region` | A contiguous address range with access count |
| `struct damos` | A DAMOS scheme (access pattern + action + quota) |
| `struct damon_operations` | Ops vtable (prepare/check/update/apply_scheme) |
| `struct damos_quota` | Rate-limiting + auto-tuning goal |
| `struct damos_watermarks` | Conditional activation based on system metric |

## Key Source Files

| File | Purpose |
|---|---|
| `mm/damon/core.c` | kdamond thread, region management, DAMOS engine |
| `mm/damon/vaddr.c` | Virtual address space monitoring ops |
| `mm/damon/paddr.c` | Physical address space monitoring ops |
| `mm/damon/reclaim.c` | DAMON_RECLAIM built-in module |
| `mm/damon/lru_sort.c` | DAMON_LRU_SORT built-in module |
| `mm/damon/sysfs.c` | Sysfs user interface |
| `include/linux/damon.h` | Public API and all data structures |

---

## Analogy

DAMON is like a **traffic monitoring camera system** for memory:

- Each **kdamond thread** is a camera crew that periodically photographs
  which memory "streets" (regions) have traffic (accesses).
- **DAMOS schemes** are automated traffic management rules: "if this street
  has been empty for 10 minutes, close it" (reclaim cold pages) or "if this
  street is always congested, widen it" (prioritize hot pages).
- The **Accessed bit** is the pavement pressure sensor — it tells the camera
  whether a car passed since the last check.
- **Adaptive region sizing** is like the camera crew dynamically merging quiet
  neighbourhoods into one view and splitting busy intersections for detail.

---

## References

- `include/linux/damon.h` — Full API
- `Documentation/mm/damon/` — Design and usage docs
- `Documentation/admin-guide/mm/damon/` — User guide
- `mm/damon/` — Full implementation
