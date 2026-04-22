# Linux Kernel devlink — Device Hardware Management

## Overview

**devlink** is a kernel infrastructure for exposing and managing **NIC/switch
hardware resources** that don't fit into existing Linux abstractions (netdev,
ethtool). It provides a Generic Netlink family for hardware parameters, firmware
info, port management, health reporters, resource pools, and packet trapping.

Used by: Mellanox/NVIDIA mlx4/mlx5, Intel ice/ixgbe/e1000e, Broadcom bnxt,
Marvell prestera, Microchip lan966x, and dozens of other smart NICs and switches.

Source: `net/devlink/`, `include/net/devlink.h`.

---

## Subsystem Stack

```
┌─────────────────────────────────────────────────────────────────┐
│                        USERSPACE                                │
│                                                                 │
│  devlink tool (iproute2)        netlink monitoring apps        │
│   devlink dev show              devlink dev eswitch set         │
│   devlink port show             devlink health show             │
│   devlink param set             devlink trap show               │
│   devlink region dump           devlink sb show                 │
└───────────────────────────────┬─────────────────────────────────┘
                                │ Generic Netlink (DEVLINK_GENL_NAME)
┌───────────────────────────────▼─────────────────────────────────┐
│                     DEVLINK NETLINK LAYER                       │
│               (net/devlink/netlink.c + netlink_gen.c)           │
│                                                                 │
│  Commands: DEVLINK_CMD_GET / PORT_GET / PARAM_SET /             │
│            HEALTH_REPORTER_GET / TRAP_SET / REGION_READ / …    │
│  Notifications: DEVLINK_CMD_PORT_NEW / HEALTH_REPORTER_RECOVER  │
└───────────────────────────────┬─────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────┐
│                      DEVLINK CORE                               │
│                 (net/devlink/core.c + dev.c)                    │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  struct devlink  (one per device)                       │   │
│  │  ports / params / resources / regions / health_list     │   │
│  │  trap_list / linecard_list / rate_nodes                 │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  ┌──────────────┐ ┌───────────────┐ ┌──────────────────────┐   │
│  │  port.c      │ │  param.c      │ │  health.c            │   │
│  │  Physical,   │ │  Driver/bus/  │ │  Health reporter:    │   │
│  │  CPU, DSA,   │ │  devlink-level│ │  diagnose, dump,     │   │
│  │  virtual     │ │  parameters   │ │  recover, auto_rec   │   │
│  │  ports       │ └───────────────┘ └──────────────────────┘   │
│  └──────────────┘                                               │
│  ┌──────────────┐ ┌───────────────┐ ┌──────────────────────┐   │
│  │  resource.c  │ │  region.c     │ │  trap.c              │   │
│  │  HW resource │ │  Memory-mapped│ │  Packet traps:       │   │
│  │  pools (e.g.,│ │  hardware     │ │  trap / mirror /     │   │
│  │  TCAM, VLANs)│ │  snapshots    │ │  drop (reason codes) │   │
│  └──────────────┘ └───────────────┘ └──────────────────────┘   │
│  ┌──────────────┐ ┌───────────────┐                             │
│  │  sb.c        │ │  rate.c       │                             │
│  │  Shared buff │ │  Egress rate  │                             │
│  │  (ingress/   │ │  limiters for │                             │
│  │   egress)    │ │  VFs/SFs      │                             │
│  └──────────────┘ └───────────────┘                             │
└───────────────────────────────┬─────────────────────────────────┘
                                │ devlink_ops callbacks
┌───────────────────────────────▼─────────────────────────────────┐
│                    HARDWARE DRIVERS                             │
│                                                                 │
│  mlx5: drivers/net/ethernet/mellanox/mlx5/core/devlink.c        │
│  ice:  drivers/net/ethernet/intel/ice/ice_devlink.c             │
│  bnxt: drivers/net/ethernet/broadcom/bnxt/bnxt_devlink.c        │
│  prestera: drivers/net/ethernet/marvell/prestera/              │
│                                                                 │
│  Register with:  devlink_alloc() → devlink_register()          │
│  Port:           devlink_port_register() + devlink_port_attrs_set
│  Param:          devlink_params_register()                      │
│  Health:         devlink_health_reporter_create()               │
│  Trap:           devlink_trap_groups_register()                │
└─────────────────────────────────────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────┐
│               PHYSICAL HARDWARE (NIC / Switch ASIC)            │
└─────────────────────────────────────────────────────────────────┘
```

---

## Layer-by-Layer Explanation

### 1. devlink Object

`struct devlink` is the root object, allocated with `devlink_alloc()`:

```c
devlink = devlink_alloc(&my_ops, sizeof(*priv), dev);
devlink_register(devlink);
```

`devlink_ops` is the driver vtable with callbacks for eswitch mode,
info_get (firmware versions), flash_update, port_split, etc.

### 2. Ports (`port.c`)

A devlink port represents a **physical front-panel port** (or virtual/CPU port
for switches). Port attributes set with `devlink_port_attrs_set()`:

| Port type | Use case |
|---|---|
| `DEVLINK_PORT_TYPE_ETH` | Standard Ethernet port |
| `DEVLINK_PORT_TYPE_IB` | InfiniBand port |
| `DEVLINK_PORT_TYPE_AUTO` | Auto-detected |

**Split ports** (breakout) allow a 100G port to appear as 4×25G:
`devlink_port_split()` / `devlink_port_unsplit()`.

**PCI functions**: PFs, VFs, SFs (Scalable Functions / subfunctions) are
registered as devlink ports with `DEVLINK_PORT_FLAVOUR_PCI_PF/VF/SF`.

### 3. Parameters (`param.c`)

Driver-level configuration parameters visible via `devlink param show/set`:

| Scope | Examples |
|---|---|
| Device | `enable_roce`, `msix_vec_per_pf`, `reset_dev_on_drv_probe` |
| Driver | `fw_load_policy`, `reset_dev_on_drv_probe` |

Type-safe values: `DEVLINK_PARAM_TYPE_U8/U16/U32/BOOL/STRING`.

### 4. Health Reporters (`health.c`)

A health reporter monitors one hardware error domain:

```c
reporter = devlink_health_reporter_create(devlink, &ops, 0, priv);
// on error:
devlink_health_report(reporter, "TX timeout", priv);
// userspace: devlink health show / recover / dump
```

Reporters support auto-recovery (`auto_recover=1`) and dump collection
(FW traces, register dumps).

### 5. Regions (`region.c`)

Hardware memory snapshots readable from userspace:

```
devlink region new pci/0000:01:00.0/cr-space
devlink region dump pci/0000:01:00.0/cr-space snapshot 1
```

Used for hardware control-register space, FW core dumps, etc.

### 6. Packet Traps (`trap.c`)

Defines which packets the hardware sends to the CPU and why:

| Action | Meaning |
|---|---|
| `DEVLINK_TRAP_ACTION_TRAP` | Send to CPU (normal path) |
| `DEVLINK_TRAP_ACTION_DROP` | Drop in hardware |
| `DEVLINK_TRAP_ACTION_MIRROR` | Send copy to CPU + forward |

Reason codes: `L2_MISS`, `ARP_REQUEST`, `DHCP`, `IPV6_UNREACH`, etc.

### 7. Shared Buffers (`sb.c`)

Physical NIC ingress/egress buffer pool management:
`devlink sb show` / `devlink sb pool set` — used for lossless Ethernet / DCB.

---

## Key Data Structures

| Structure | Purpose |
|---|---|
| `struct devlink` | Root per-device object |
| `struct devlink_ops` | Driver callbacks vtable |
| `struct devlink_port` | Physical/virtual port |
| `struct devlink_param` | Configuration parameter |
| `struct devlink_health_reporter` | Error domain reporter |
| `struct devlink_region` | HW memory snapshot |
| `struct devlink_trap` | Packet trap definition |

## Key Source Files

| File | Purpose |
|---|---|
| `net/devlink/core.c` | Devlink alloc/register/unregister |
| `net/devlink/netlink.c` | Netlink command handlers |
| `net/devlink/port.c` | Port registration and attributes |
| `net/devlink/param.c` | Parameter get/set |
| `net/devlink/health.c` | Health reporter framework |
| `net/devlink/region.c` | HW memory snapshot regions |
| `net/devlink/trap.c` | Packet trap management |
| `net/devlink/sb.c` | Shared buffer management |
| `net/devlink/rate.c` | Egress rate limiting |
| `include/net/devlink.h` | Public API |

---

## Quick Reference

```bash
# Show all devlink devices
devlink dev show

# Firmware version info
devlink dev info pci/0000:01:00.0

# Flash firmware
devlink dev flash pci/0000:01:00.0 file fw.bin

# Show ports
devlink port show

# Split 100G port into 4×25G
devlink port split pci/0000:01:00.0/0 count 4

# Parameters
devlink dev param show pci/0000:01:00.0
devlink dev param set pci/0000:01:00.0 name enable_roce value true cmode driverinit

# Health
devlink health show
devlink health recover pci/0000:01:00.0 reporter tx

# Trap
devlink trap show pci/0000:01:00.0
devlink trap set pci/0000:01:00.0 trap arp_request action trap
```

---

## Analogy

devlink is like the **maintenance panel** of an aircraft:

- The **netdev** (normal Linux network device) is the passenger interface — what
  you interact with every flight.
- **devlink** is the cockpit maintenance panel — it exposes hardware internals:
  firmware versions, hardware resource allocation, fault diagnostics, and
  port wiring.
- A **health reporter** is the aircraft's BITE (Built-In Test Equipment) —
  it monitors subsystems and reports faults to ground crew.
- **Packet traps** are air traffic control rules — certain packets get "trapped"
  to the control plane instead of being forwarded.

---

## References

- `include/net/devlink.h` — Full driver API
- `include/uapi/linux/devlink.h` — Netlink ABI
- `Documentation/networking/devlink/` — Per-subsystem docs
- `net/devlink/` — Implementation
