# psample — Packet Sampling Framework

## Overview

The psample subsystem provides a generic packet sampling framework in the Linux
kernel. It enables network devices and tc (traffic control) actions to sample
packets and export them to user space via Generic Netlink. This is the kernel-side
foundation for:

- **sFlow** — Industry-standard packet sampling protocol
- **Mirror-on-drop** — Sample packets that are dropped
- **tc sample action** — Traffic control sampling integration

psample uses **sampling groups** to organize sampled traffic. Each group has a
numeric ID, and user-space collectors (like hsflowd or psample-listener) subscribe
to specific groups via netlink multicast.

## Kernel Source

- **Directory:** `net/psample/`
- **Headers:** `include/net/psample.h`
- **Config:** `CONFIG_PSAMPLE`

## Architecture

```
┌─────────────────────────────────────────────┐
│         User Space Collectors               │
│    hsflowd, psample-listener, custom        │
│    (Generic Netlink PSAMPLE family)         │
├─────────────────────────────────────────────┤
│        Generic Netlink (genetlink)          │
│     PSAMPLE_NL_MCGRP_SAMPLE multicast      │
├─────────────────────────────────────────────┤
│          psample Core                       │
│  ┌────────────────────────────────┐         │
│  │  psample_sample_packet()      │         │
│  │  Build netlink sample msg     │         │
│  │  Include: data, metadata,     │         │
│  │  group, rate, seq, iif, oif   │         │
│  └────────────────────────────────┘         │
│  ┌────────────────────────────────┐         │
│  │  psample_group management     │         │
│  │  get/put reference counting   │         │
│  └────────────────────────────────┘         │
├──────────────┬──────────────────────────────┤
│              │                              │
│   tc action  │    Hardware offload          │
│   act_sample │    (mlxsw, bnxt, etc.)       │
│              │    Driver calls psample      │
│              │    _sample_packet() directly  │
└──────────────┴──────────────────────────────┘
```

## Workflow

```
 SAMPLING SETUP                          PACKET SAMPLING
 ──────────────                          ───────────────

 User configures tc:                    Packet hits tc rule
 tc filter add ... \                    or hardware samples
   action sample \                           │
   rate 100 group 1                          ▼
     │                               act_sample triggers
     ▼                                or driver calls
 psample_group_get(1)                        │
     │                                       ▼
     ▼                               psample_sample_packet()
 Create/refcount                             │
 psample_group #1                            ▼
     │                               Build genetlink msg:
     ▼                               PSAMPLE_ATTR_SAMPLE_GROUP
 Group ready                          PSAMPLE_ATTR_DATA (pkt)
                                      PSAMPLE_ATTR_SAMPLE_RATE
                                      PSAMPLE_ATTR_IIFINDEX
                                             │
                                             ▼
                                      genlmsg_multicast()
                                             │
                                             ▼
                                      User-space collector
                                      receives sample
```

## Key Structures

| Structure | File | Purpose |
|-----------|------|---------|
| `struct psample_group` | `include/net/psample.h` | Sampling group — identified by group_num |
| `struct psample_metadata` | `include/net/psample.h` | Per-sample metadata (rate, seq, tunnel) |

## Key Functions

| Function | File | Purpose |
|----------|------|---------|
| `psample_sample_packet()` | `net/psample/psample.c` | Sample a packet — build and send netlink msg |
| `psample_group_get()` | `net/psample/psample.c` | Get/create a sampling group by ID |
| `psample_group_put()` | `net/psample/psample.c` | Release reference to a sampling group |
| `psample_group_take()` | `net/psample/psample.c` | Take additional reference to group |

## Analogy

psample is like a **quality control inspector on an assembly line**. Instead of
examining every product (packet), the inspector samples 1 in every N items
(sampling rate) and puts them on a side table (netlink multicast group) for the
quality team (user-space collector) to analyze. Different inspection stations
(tc rules, hardware offload) can all send samples to the same table. The inspector
doesn't modify the products — they just take a snapshot and let the original
continue down the line.
