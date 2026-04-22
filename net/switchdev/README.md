# switchdev — Software Switch Device Offload API

## Overview

**switchdev** is a Linux kernel framework that lets **hardware network switches
and smart NICs offload the Linux bridge/router data path** into silicon.  When
a switch chip (e.g., Marvell Prestera, Microchip LAN966x, Mellanox Spectrum)
is represented as a set of Linux `net_device` port interfaces, the switchdev
API lets the bridge/routing layer push FDB entries, VLAN tables, STP states,
and ACL rules directly to hardware.

Key capabilities:
- **FDB (Forwarding DataBase) offload** — hardware learns and ages MAC entries
- **VLAN offload** — PVID/trunk VLAN programming in hardware
- **STP port state** offload — prune hardware ports per spanning tree state
- **MDB (Multicast DataBase)** offload — hardware IGMP snooping tables
- **Port attributes** — speed, STP state, learning on/off, flood control
- **Deferred work queue** — async attribute/object updates from atomic context
- **Blocking notifier** for event-driven config (bridge ↔ driver)

Source: `net/switchdev/switchdev.c`, `include/net/switchdev.h`.

---

## Subsystem Stack

```
┌────────────────────────────────────────────────────────────────┐
│                        USERSPACE                               │
│  bridge  iproute2  mstpd  tc  devlink                         │
└──────────────────────────────┬─────────────────────────────────┘
                               │ netlink / ioctl
┌──────────────────────────────▼─────────────────────────────────┐
│            LINUX BRIDGE / ROUTING LAYER                        │
│                                                                 │
│  br_fdb_add / br_fdb_delete → switchdev FDB notifications      │
│  br_vlan_add / br_vlan_delete → switchdev VLAN object ops      │
│  br_set_state (STP) → switchdev port attribute                 │
│  bridge MDB → switchdev MDB object                            │
└──────────────────────────────┬─────────────────────────────────┘
                               │
┌──────────────────────────────▼─────────────────────────────────┐
│            SWITCHDEV CORE  (net/switchdev/switchdev.c)         │
│                                                                 │
│  Attribute ops (port-attr):                                    │
│   switchdev_port_attr_set()                                    │
│   → SWITCHDEV_ATTR_ID_PORT_STP_STATE                           │
│   → SWITCHDEV_ATTR_ID_PORT_BRIDGE_FLAGS                        │
│   → SWITCHDEV_ATTR_ID_PORT_PRE_BRIDGE_FLAGS                    │
│   → SWITCHDEV_ATTR_ID_BRIDGE_VLAN_FILTERING                    │
│   → SWITCHDEV_ATTR_ID_BRIDGE_AGEING_TIME                       │
│                                                                 │
│  Object ops (port-obj):                                        │
│   switchdev_port_obj_add() / _del() / _dump()                  │
│   → SWITCHDEV_OBJ_ID_PORT_VLAN                                 │
│   → SWITCHDEV_OBJ_ID_PORT_MDB                                  │
│   → SWITCHDEV_OBJ_ID_HOST_MDB                                  │
│                                                                 │
│  Deferred work queue:                                          │
│   switchdev_deferred_enqueue() → switchdev_port_deferred_work()│
│   (allows attr/obj updates from atomic / notifier context)     │
│                                                                 │
│  Event notifiers:                                              │
│   SWITCHDEV_FDB_ADD_TO_BRIDGE / _DEL_TO_BRIDGE                 │
│   SWITCHDEV_FDB_ADD_TO_DEVICE / _DEL_TO_DEVICE                 │
│   SWITCHDEV_PORT_OBJ_ADD / _DEL                                │
└──────────────────────────────┬─────────────────────────────────┘
                               │ ndo_switchdev_port_attr_set
                               │ ndo_switchdev_port_obj_add
                               │ ndo_switchdev_port_obj_del
┌──────────────────────────────▼─────────────────────────────────┐
│         NIC / SWITCH DRIVER  (e.g., mlxsw, prestera, ocelot,   │
│              dsa, lan966x, rzn1-a5psw, hellcreek, sja1105)     │
│                                                                 │
│  Implements switchdev_ops in net_device:                       │
│   .ndo_get_phys_port_name  — human-readable port name          │
│   .ndo_get_port_parent_id  — unique switch chip identifier     │
│                                                                 │
│  Registers notifiers to receive FDB/VLAN/MDB changes and       │
│  programs them into hardware ASIC tables                       │
└──────────────────────────────┬─────────────────────────────────┘
                               │  PCI / MDIO / SPI / etc.
┌──────────────────────────────▼─────────────────────────────────┐
│            SWITCH / SMART-NIC HARDWARE ASIC                    │
│   L2 forwarding table  VLAN table  Port state  MDB table       │
└────────────────────────────────────────────────────────────────┘
```

---

## Data Flow: FDB Entry Offload

```
Bridge learns MAC on port sw0p1
         │
         ▼
br_fdb_add() → SWITCHDEV_FDB_ADD_TO_DEVICE notifier
         │
         ▼
Driver's switchdev notifier fires
         │
         ▼
Driver writes MAC+VID to ASIC FDB table
         │
Hardware forwards future frames in silicon (no CPU involvement)
```

---

## Key Data Structures

| Structure | Purpose |
|---|---|
| `switchdev_attr` | A port attribute (STP state, ageing time, VLAN filtering…) |
| `switchdev_obj` | A programmable object (VLAN, MDB entry) |
| `switchdev_obj_port_vlan` | VLAN to program on a port (vid, flags) |
| `switchdev_obj_port_mdb` | Multicast group entry (MAC + VID) |
| `switchdev_notifier_fdb_info` | FDB add/del event payload |
| `switchdev_deferred_item` | Deferred attr/obj update (workqueue) |

---

## Key Source Files

| File | Purpose |
|---|---|
| `net/switchdev/switchdev.c` | Core: attr ops, object ops, deferred queue, notifiers |
| `include/net/switchdev.h` | Public API: structures, ops, notifier IDs |
| `net/bridge/br_switchdev.c` | Bridge-side integration |
| `drivers/net/ethernet/mellanox/mlxsw/` | Example switchdev driver |
| `drivers/net/dsa/` | DSA (Distributed Switch Architecture) switchdev users |

---

## Analogy

switchdev is like a **BIOS/firmware interface for a managed Ethernet switch**:

- The **Linux bridge** is the OS that decides which traffic should go where.
- **switchdev** is the firmware interface that says "now program these decisions
  directly into the hardware forwarding table."
- The **switch driver** is the BIOS — it knows the hardware registers and
  programs the ASIC tables when the OS tells it to.
- Without switchdev, every packet would bounce up to the CPU even when the
  hardware could forward it at line rate.  With switchdev, the CPU is only
  involved for exceptions (new MACs, topology changes, IGMP joins).

---

## References

- `include/net/switchdev.h` — full API
- `net/switchdev/switchdev.c` — implementation
- `Documentation/networking/switchdev.rst`
