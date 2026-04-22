# Linux Kernel ethtool — Network Interface Configuration

## Overview

**ethtool** is the standard Linux interface for **configuring and querying
network interface card (NIC) hardware settings**: link speed, duplex, offloads,
coalescing, ring sizes, queues, statistics, diagnostics, and more.

The subsystem has two interfaces:
1. **Legacy ioctl** (`SIOCETHTOOL`) — original, struct-based, limited extensibility
2. **ethtool Netlink** (introduced 5.6) — Generic Netlink, extensible, asynchronous
   notifications, used by modern `ethtool` versions

Source: `net/ethtool/`, `include/linux/ethtool.h`, `include/uapi/linux/ethtool.h`.

---

## Subsystem Stack

```
┌─────────────────────────────────────────────────────────────────┐
│                        USERSPACE                                │
│                                                                 │
│   ethtool eth0                 iproute2 / nmcli / python-ethtool│
│     ├── ethtool -s eth0 speed 1000 duplex full autoneg on       │
│     ├── ethtool -G eth0 rx 4096 tx 4096                        │
│     ├── ethtool -k eth0  (show offloads)                       │
│     └── ethtool -S eth0  (statistics)                          │
└──────────────────────────────┬──────────────────────────────────┘
                               │
              ┌────────────────┴─────────────────────┐
              │                                      │
     ioctl SIOCETHTOOL                 Generic Netlink (ethtool)
     (net/ethtool/ioctl.c)             (net/ethtool/netlink.c)
              │                                      │
┌─────────────▼──────────────────────────────────────▼───────────┐
│                     ETHTOOL CORE                               │
│                                                                 │
│  Validates request → calls ops on struct net_device →          │
│  serializes response to user                                   │
│                                                                 │
│  Per-feature handlers:                                         │
│  linkinfo.c    linkmodes.c   linkstate.c   debug.c             │
│  features.c    rings.c       channels.c    coalesce.c          │
│  pause.c       eee.c         stats.c       rss.c               │
│  eeprom.c      module.c      cabletest.c   ts{info,config}.c   │
│  phc_vclocks.c wol.c         privflags.c   strset.c bitset.c   │
│  pse-pd.c      plca.c        mm.c          tunnels.c fec.c     │
└──────────────────────────────┬──────────────────────────────────┘
                               │ ethtool_ops callbacks
┌──────────────────────────────▼──────────────────────────────────┐
│                   NIC DRIVER  (e.g., igc, e1000e, mlx5, bnxt)  │
│                                                                 │
│  struct ethtool_ops:                                           │
│   .get_link_ksettings / .set_link_ksettings   ← speed/duplex   │
│   .get_ringparam / .set_ringparam             ← ring sizes     │
│   .get_channels / .set_channels               ← queue count    │
│   .get_coalesce / .set_coalesce               ← IRQ coalescing │
│   .get_features / .set_features               ← HW offloads    │
│   .get_ethtool_stats                           ← HW counters   │
│   .get_strings                                 ← stat names    │
│   .begin_cable_test / .complete_cable_test     ← TDR test      │
│   .get_eeprom / .set_eeprom                   ← NVM access     │
│   .get_module_info / .get_module_eeprom_by_page← SFP/QSFP      │
│   .get_ts_info                                ← PTP/timestamping│
└──────────────────────────────┬──────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────┐
│                   PHYSICAL NETWORK HARDWARE                     │
│   NIC ASIC  ──  PHY chip  ──  cable/SFP module  ──  link       │
└─────────────────────────────────────────────────────────────────┘
```

---

## Layer-by-Layer Explanation

### 1. Legacy ioctl Path (`ioctl.c`)

`SIOCETHTOOL` dispatches to `dev_ethtool()`, which switches on the `ethcmd`
field of the user structure. Each command maps to one or two `ethtool_ops`
callbacks. The ioctl interface is frozen — no new commands are added; all new
features use the Netlink API.

### 2. ethtool Netlink API (`netlink.c`)

A Generic Netlink family (`ethtool_genl_family`) with:
- **GET/SET/ACT** message types per feature (link modes, rings, channels, …)
- **Compact bitsets** for link mode reporting
- **Asynchronous notifications** — drivers can call `ethtool_notify()` to push
  state changes to userspace subscribers
- **ETHTOOL_MSG_LINKSTATE_NTF** etc. for link state changes

### 3. Link Settings (`linkmodes.c`, `linkstate.c`)

`ethtool_link_ksettings` replaces the old `ethtool_cmd`:
- `link_modes.supported` / `.advertising` / `.lp_advertising` — bitmasks of
  supported link modes (1000baseT-Full, 25000baseSR-Full, etc.)
- Speed/duplex/autoneg/port/MDI-X settings

### 4. Hardware Offloads (`features.c`)

`NETIF_F_*` flags (TSO, GSO, LRO, RX/TX checksum, VLAN offload, etc.) controlled
via `ethtool -K` / `ETHTOOL_GFEATURES` / `ETHTOOL_SFEATURES`.

### 5. Ring Parameters and Channels (`rings.c`, `channels.c`)

- **Ring sizes** (`-G`): RX/TX descriptor ring depths; larger = more buffering, more memory.
- **Channels** (`-L`): number of RX/TX queue pairs; must match CPU/IRQ affinity.

### 6. IRQ Coalescing (`coalesce.c`)

Batches interrupts for performance:
- `rx_coalesce_usecs` / `rx_max_coalesced_frames` — wait before raising IRQ
- Adaptive coalescing: kernel auto-tunes based on traffic load

### 7. Statistics (`stats.c`, `strset.c`)

Drivers export named counters via `.get_strings()` + `.get_ethtool_stats()`.
Queried with `ethtool -S eth0`. The Netlink API adds structured, per-queue stats.

### 8. Cable Diagnostics (`cabletest.c`)

TDR (Time-Domain Reflectometry) cable tests via `ethtool --cable-test eth0`.
Results reported asynchronously via Netlink notifications.

### 9. Module / Transceiver (`module.c`, `eeprom.c`)

SFP/QSFP/DSFP module info, EEPROM read/write, CMIS (Common Management Interface
Specification) firmware update (`net/ethtool/cmis_fw_update.c`).

### 10. Timestamping (`tsinfo.c`, `tsconfig.c`)

Reports PTP hardware clock capabilities and configures hardware RX/TX timestamping
modes for `SO_TIMESTAMPING` / PTP4L.

---

## Common Operations

```bash
# Show link settings
ethtool eth0

# Force 1G full-duplex
ethtool -s eth0 speed 1000 duplex full autoneg off

# Show and set ring sizes
ethtool -g eth0
ethtool -G eth0 rx 4096 tx 4096

# Show and set queue count
ethtool -l eth0
ethtool -L eth0 combined 4

# Show/set coalescing
ethtool -c eth0
ethtool -C eth0 rx-usecs 50

# Show offload features
ethtool -k eth0

# Show hardware statistics
ethtool -S eth0

# SFP module info
ethtool -m eth0

# Cable test
ethtool --cable-test eth0

# PTP timestamping info
ethtool -T eth0
```

---

## Key Data Structures

| Structure | Purpose |
|---|---|
| `ethtool_ops` | NIC driver vtable (get/set for every feature) |
| `ethtool_link_ksettings` | Link speed, duplex, advertised modes |
| `ethtool_ringparam` | RX/TX descriptor ring sizes |
| `ethtool_channels` | RX/TX/combined queue count |
| `ethtool_coalesce` | IRQ coalescing parameters |
| `ethtool_stats` | Hardware counter array |
| `kernel_ethtool_ts_info` | PTP/timestamping capabilities |

## Key Source Files

| File | Purpose |
|---|---|
| `net/ethtool/netlink.c` | Generic Netlink family registration and dispatch |
| `net/ethtool/ioctl.c` | Legacy SIOCETHTOOL ioctl |
| `net/ethtool/linkmodes.c` | Link mode negotiation |
| `net/ethtool/linkstate.c` | Link state reporting |
| `net/ethtool/features.c` | Hardware offload feature flags |
| `net/ethtool/rings.c` | Ring buffer size |
| `net/ethtool/channels.c` | Queue/channel count |
| `net/ethtool/coalesce.c` | IRQ coalescing |
| `net/ethtool/stats.c` | Hardware statistics |
| `net/ethtool/module.c` | SFP/QSFP transceiver management |
| `net/ethtool/tsinfo.c` | PTP timestamping info |
| `include/linux/ethtool.h` | `ethtool_ops` vtable |
| `include/uapi/linux/ethtool.h` | UAPI structs and constants |

---

## Analogy

ethtool is like the **settings panel for a high-end audio amplifier**:

- The **NIC driver** is the amplifier hardware with dozens of adjustable knobs.
- **ethtool** is the settings panel that exposes those knobs in a standard way.
- The **ioctl interface** is the old analog knob panel — works but can't be
  extended beyond the original design.
- The **Netlink interface** is a modern digital display with live metering,
  presets, and async alerts when something changes.
- The **driver's `ethtool_ops`** is the connector strip behind the panel that
  wires each knob to the actual hardware register.

---

## References

- `include/linux/ethtool.h` — `ethtool_ops` vtable
- `include/uapi/linux/ethtool.h` — UAPI constants
- `Documentation/networking/ethtool-netlink.rst`
- `net/ethtool/` — Implementation
