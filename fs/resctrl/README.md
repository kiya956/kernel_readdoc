# resctrl — Resource Control Filesystem (Intel RDT / AMD MSRC)

## Overview

**resctrl** is a Linux pseudo-filesystem (`/sys/fs/resctrl`) that exposes
**Intel Resource Director Technology (RDT)** and **AMD Memory System Resource
Controller (MSRC)** to userspace.  It allows system administrators to:

- **Partition LLC (Last-Level Cache)** between groups of tasks using Cache
  Allocation Technology (CAT)
- **Limit memory bandwidth** using Memory Bandwidth Allocation (MBA)
- **Monitor LLC occupancy and memory bandwidth** per task group using
  Cache Monitoring Technology (CMT) and Memory Bandwidth Monitoring (MBM)
- **Pseudo-lock** cache ways to dedicate them to latency-sensitive workloads

Implemented as a **kernfs-based filesystem** (`RESCTRL_FS_MAGIC`), it appears
under `/sys/fs/resctrl/` and works via directory creation (groups), writing to
`schemata` files (policy), and reading `mon_data/` (monitoring).

Source: `fs/resctrl/`, `arch/x86/kernel/cpu/resctrl/`, `include/linux/resctrl.h`.

---

## Subsystem Stack

```
┌────────────────────────────────────────────────────────────────┐
│                        USERSPACE                               │
│  mount -t resctrl resctrl /sys/fs/resctrl                     │
│  mkdir /sys/fs/resctrl/db_group      ← create CTRL_MON group  │
│  echo "L3:0=ff;1=ff\nMB:0=40;1=40" > .../schemata ← policy   │
│  echo <pid> > .../tasks             ← assign tasks            │
│  cat .../mon_data/mon_L3_00/llc_occupancy ← read CMT data     │
└──────────────────────────────┬─────────────────────────────────┘
                               │ VFS / kernfs
┌──────────────────────────────▼─────────────────────────────────┐
│            RESCTRL FILESYSTEM (fs/resctrl/rdtgroup.c)          │
│                                                                 │
│  Mount: rdtgroup_setup_root() → creates kernfs tree            │
│  Group types:                                                  │
│    CTRL_MON — control + monitoring group (top-level dir)       │
│    MON       — monitoring-only subgroup (mon_groups/)          │
│                                                                 │
│  Per-group files:                                              │
│    schemata  — read/write cache/bandwidth allocation bitmasks  │
│    tasks     — read/write PIDs; moves them into this group     │
│    cpus      — CPUID affinity for this group                   │
│    mon_data/ — per-CPU/per-RMID monitoring counters            │
└──────────────────────────────┬─────────────────────────────────┘
                               │
┌──────────────────────────────▼─────────────────────────────────┐
│            MONITOR (fs/resctrl/monitor.c)                      │
│                                                                 │
│  CMT: LLC occupancy per RMID (Resource Monitoring ID)          │
│  MBM: Total / local memory bandwidth per RMID                  │
│  RMID allocation pool: rmid_alloc / rmid_free                  │
│  Periodic monitoring workqueue: reads RMID counters via MSR    │
└──────────────────────────────┬─────────────────────────────────┘
                               │
┌──────────────────────────────▼─────────────────────────────────┐
│         ARCHITECTURE BACKEND  (arch/x86/kernel/cpu/resctrl/)   │
│                                                                 │
│  Intel RDT:                                                    │
│   resctrl_arch_update_domains() — programs IA32_L3_QOS_MASK_n  │
│   MBA: programs IA32_L2_QOS_BW_THRTL_n                        │
│   CMT/MBM: reads IA32_QM_CTR via IA32_QM_EVTSEL               │
│                                                                 │
│  Per-CPU MSR programming on domain change                      │
│  CPUID leaf 0x10 / 0x7 detect CAT/MBA/CMT/MBM support         │
└──────────────────────────────┬─────────────────────────────────┘
                               │  MSR / CPUID
┌──────────────────────────────▼─────────────────────────────────┐
│            CPU HARDWARE  (Intel RDT / AMD MSRC)                │
│   LLC partitioning, bandwidth throttling, occupancy monitoring │
└────────────────────────────────────────────────────────────────┘
```

---

## Key Concepts

### CLOSID (CLOSure ID)
Each `CTRL_MON` group has a unique 4-bit hardware CLOSID.  The CPU looks up the
CLOSID in the QoS mask register to determine which LLC ways this group owns.

### RMID (Resource Monitoring ID)
Each `MON` group has a unique RMID.  The hardware counts LLC occupancy and
memory bandwidth per active RMID and stores them in per-RMID counters readable
via `IA32_QM_CTR`.

### Schemata Format
```
L3:0=ff;1=ff      # socket 0: all 8 LLC ways; socket 1: all 8 LLC ways
MB:0=50;1=100     # socket 0: 50% max bandwidth; socket 1: unrestricted
```

---

## Key Data Structures

| Structure | Purpose |
|---|---|
| `rdtgroup` | One resctrl group (CTRL_MON or MON): CLOSID, RMID, tasks |
| `resctrl_schema` | One resource (L3 cache or MB) with its domains |
| `rdt_domain` | Per-socket resource state (bitmask, bandwidth, RMID counters) |
| `rmid_read` | Per-RMID monitoring counter read result |
| `pseudo_lock_region` | Cache pseudo-lock configuration |

---

## Key Source Files

| File | Purpose |
|---|---|
| `fs/resctrl/rdtgroup.c` | Filesystem mount, group lifecycle, schemata/tasks files |
| `fs/resctrl/monitor.c` | CMT/MBM monitoring, RMID management |
| `fs/resctrl/ctrlmondata.c` | schemata parsing and validation |
| `fs/resctrl/pseudo_lock.c` | Cache pseudo-locking |
| `arch/x86/kernel/cpu/resctrl/core.c` | RDT feature detection + MSR programming |
| `include/linux/resctrl.h` | Architecture-agnostic resctrl API |

---

## Analogy

resctrl is like a **meeting room booking system for a shared office (CPU)**:

- The **LLC** is a large shared meeting room (cache).
- Each **CTRL_MON group** is a department that gets a reserved section of the
  meeting room (cache ways via CAT bitmask).
- **Memory bandwidth** is the office's internet bandwidth, and MBA limits how
  much of it each department can use.
- **CMT/MBM monitoring** is the occupancy sensor and bandwidth meter: you can
  see how much of the reserved room each department is actually using.
- The **schemata file** is the booking form: write it to reconfigure
  the allocation policy.

---

## References

- `fs/resctrl/` — filesystem implementation
- `arch/x86/kernel/cpu/resctrl/` — x86 backend
- `include/linux/resctrl.h` — API
- `Documentation/arch/x86/resctrl.rst`
- Intel SDM Vol. 3, Chapter 17 (RDT)
