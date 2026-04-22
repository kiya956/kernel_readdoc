# net/shaper — Kernel Network Shaper (Bandwidth Shaping)

## Overview

`net/shaper/` is the **in-kernel network shaper infrastructure** added in
Linux 6.9.  It provides a **hierarchical bandwidth shaping** API exposed
via **Generic Netlink** (`NET_SHAPER_FAMILY`), allowing NIC drivers to
implement hardware-offloaded rate limiting and shaping in a unified way.

Unlike classic `tc qdisc` (which is purely software), the shaper API
targets **NIC hardware shaping engines** (e.g., RDMA NICs with built-in
rate limiters), while keeping a common user-space interface.

Key concepts:
- **Binding** — a shaper is bound to a `netdev` (future: queue/channel)
- **Hierarchy** — shapers form a tree; child's bandwidth is capped by parent
- **Scope + ID** — a `u32` handle encoding scope (26 MSBs) and ID (26 LSBs)
- **Metrics** — `bw_min` (guaranteed), `bw_max` (peak), `burst` (bytes),
  `priority` (scheduling weight)
- **XArray** — shaper hierarchy stored in `netdev->net_shaper_hierarchy`

Source: `net/shaper/shaper.c`, `include/net/net_shaper.h`

---

## Subsystem Stack

```
┌──────────────────────────────────────────────────────────────────┐
│                 User Space (iproute2, devlink tool)              │
│   genl_send(NET_SHAPER_FAMILY, NET_SHAPER_CMD_SET / GET / DEL)   │
└───────────────────────────┬──────────────────────────────────────┘
                            │  Generic Netlink
┌───────────────────────────▼──────────────────────────────────────┐
│              net/shaper/shaper.c  (shaper core)                  │
│              net/shaper/shaper_nl_gen.c  (genl op table)         │
│                                                                  │
│  net_shaper_nl_set_doit()   — create/update shaper               │
│  net_shaper_nl_get_doit()   — read shaper config                 │
│  net_shaper_nl_delete_doit() — remove shaper                     │
│  net_shaper_nl_group_doit()  — set a group of shapers            │
│                                                                  │
│  Locking: netdev_lock() protects hierarchy xarray                │
│  Hierarchy stored in netdev->net_shaper_hierarchy (xarray)       │
└───────────────────────────┬──────────────────────────────────────┘
                            │  calls driver's net_shaper_ops
┌───────────────────────────▼──────────────────────────────────────┐
│              NIC Driver (e.g., mlx5, bnxt, ionic)                │
│              netdev_ops->net_shaper_ops:                         │
│                .set()    — program hardware shaper               │
│                .del()    — remove hardware shaper                │
│                .group()  — atomic group update                   │
└───────────────────────────┬──────────────────────────────────────┘
                            │
┌───────────────────────────▼──────────────────────────────────────┐
│              NIC Hardware Shaping Engine                         │
│              (rate limiter, scheduler, token bucket in HW)       │
└──────────────────────────────────────────────────────────────────┘
```

---

## Shaper Hierarchy Example

```
Netdev eth0
│
├── Shaper (scope=PORT, id=0)     bw_max=10Gbps  ← root cap
│   ├── Shaper (scope=QUEUE, id=0)  bw_max=4Gbps, priority=HIGH
│   ├── Shaper (scope=QUEUE, id=1)  bw_max=4Gbps, priority=NORMAL
│   └── Shaper (scope=QUEUE, id=2)  bw_min=200Mbps, bw_max=2Gbps
```

---

## Key Data Structures

| Structure | File | Purpose |
|---|---|---|
| `net_shaper` | `include/net/net_shaper.h` | One shaper node: id, bw_min/max, burst, prio |
| `net_shaper_hierarchy` | `shaper.c` | XArray of all shapers on a binding |
| `net_shaper_binding` | `include/net/net_shaper.h` | Binding target: netdev (or future queue) |
| `net_shaper_ops` | `include/net/net_shaper.h` | Driver callback table: set/del/group |
| `net_shaper_nl_ctx` | `shaper.c` | Per-op context for Netlink handler |

---

## Key Source Files

| File | Purpose |
|---|---|
| `net/shaper/shaper.c` | Core: hierarchy management, driver dispatch |
| `net/shaper/shaper_nl_gen.c` | Auto-generated Netlink op dispatch |
| `net/shaper/shaper_nl_gen.h` | Generated header |
| `include/net/net_shaper.h` | Public API: structures and net_shaper_ops |

---

## Analogy

net/shaper is like a **hotel elevator reservation system**:

- The **netdev** is the hotel building with limited elevator capacity (link
  bandwidth).
- Each **shaper** is a time slot reservation: "VIP guests get 40% of
  capacity, always; economy guests share the rest."
- The **hierarchy** means sub-groups (floors, departments) have their own
  sub-reservations that must fit within the parent's allocation.
- The **driver's `net_shaper_ops`** is the physical elevator control panel —
  the system sends it the policy, the hardware enforces it.
- Generic Netlink is the hotel app that lets the concierge (admin) update
  the reservation policy without rebooting the elevator system.

---

## References

- `net/shaper/shaper.c` — implementation
- `include/net/net_shaper.h` — public API
- Linux kernel commit adding net_shaper (v6.9)
- `Documentation/netlink/specs/net_shaper.yaml` — Netlink spec
