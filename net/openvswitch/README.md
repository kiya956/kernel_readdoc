# Open vSwitch — Kernel Datapath

## Overview

**Open vSwitch (OvS)** is a production-quality multilayer virtual switch.
The kernel module (`openvswitch.ko`) implements the **datapath** — a
high-performance flow-based packet processing engine:

- **Flow table** — hash table of `(key → actions)` entries for fast-path
  packet forwarding without userspace involvement
- **Flow key extraction** — parse packet headers into a `sw_flow_key`
  (L2/L3/L4 fields, tunnel metadata, VLAN tags)
- **Actions** — output to port, set fields, push/pop VLAN, encap/decap,
  sample, CT (conntrack), recirculate
- **Upcall** — on flow table miss, send packet to `ovs-vswitchd` daemon
  via Netlink; daemon installs a flow for future packets
- **Vports** — virtual ports: netdev, internal, VXLAN, Geneve, GRE, LISP

Source: `net/openvswitch/`, `include/uapi/linux/openvswitch.h`.

---

## Subsystem Stack

```
┌──────────────────────────────────────────────────────────────────┐
│                    ovs-vswitchd (userspace)                       │
│  OpenFlow controller, flow programming, OVSDB                    │
│  ↕ OVS Netlink (OVS_DATAPATH_FAMILY, OVS_FLOW_FAMILY, …)       │
└──────────────────────────────┬───────────────────────────────────┘
                               │ Netlink (upcall / flow install)
┌──────────────────────────────▼───────────────────────────────────┐
│              OVS KERNEL DATAPATH  (net/openvswitch/)             │
│                                                                   │
│  ovs_vport_receive()                                             │
│    → extract sw_flow_key from packet headers                     │
│    → ovs_dp_process_packet()                                     │
│                                                                   │
│  ovs_dp_process_packet()                                         │
│    → ovs_flow_tbl_lookup() — search flow table                  │
│    ┌─ HIT:  ovs_execute_actions() — apply cached actions         │
│    └─ MISS: ovs_dp_upcall() — send to ovs-vswitchd              │
│                                                                   │
│  ┌────────────────────────────────────────────────────────┐      │
│  │  FLOW TABLE (struct flow_table)                         │      │
│  │   sw_flow_key  →  sw_flow_actions                      │      │
│  │   (L2+L3+L4+tunnel+CT hash)                           │      │
│  │                                                         │      │
│  │  Actions: OVS_ACTION_ATTR_OUTPUT                       │      │
│  │           OVS_ACTION_ATTR_SET                          │      │
│  │           OVS_ACTION_ATTR_PUSH_VLAN / POP_VLAN        │      │
│  │           OVS_ACTION_ATTR_CT (conntrack)               │      │
│  │           OVS_ACTION_ATTR_RECIRC                       │      │
│  └────────────────────────────────────────────────────────┘      │
│                                                                   │
│  VPORTS:  netdev | internal | vxlan | geneve | gre               │
└──────────────────────────────────────────────────────────────────┘
```

---

## Packet Flow

```
  NIC → netdev vport → ovs_vport_receive()
    → key extraction (parse ETH/IP/TCP/UDP/ARP/ICMP headers)
    → ovs_dp_process_packet()
      ├─ Flow HIT:
      │    ovs_execute_actions(flow->sf_acts)
      │      → output to vport
      │      → modify headers (set src/dst MAC/IP)
      │      → push/pop VLAN
      │      → conntrack (CT)
      │      → encapsulate (VXLAN/Geneve tunnel)
      └─ Flow MISS:
           ovs_dp_upcall() → Netlink → ovs-vswitchd
             → OpenFlow pipeline
             → ovs_flow_cmd_new() — install flow in kernel
             → ovs_packet_cmd_execute() — reinject packet
```

---

## Key Data Structures

| Structure | Purpose |
|---|---|
| `struct datapath` | OvS datapath instance: flow table, vport list, stats |
| `struct sw_flow` | Single flow entry: key + mask + actions + stats |
| `struct sw_flow_key` | Parsed packet headers: L2/L3/L4/tunnel/CT fields |
| `struct sw_flow_actions` | Action list: output, set, push_vlan, CT, etc. |
| `struct vport` | Virtual port: type (netdev/internal/tunnel), ops |
| `struct flow_table` | Hash table of flows with mask caching |

---

## Key Functions

| Function | Role |
|---|---|
| `ovs_dp_process_packet()` | Main packet processing: lookup → actions or upcall |
| `ovs_flow_tbl_lookup()` | Search flow table for matching flow |
| `ovs_execute_actions()` | Execute action list on a packet |
| `ovs_dp_upcall()` | Send miss/sample to userspace ovs-vswitchd |
| `ovs_vport_receive()` | Vport receive: extract key, enter datapath |
| `ovs_vport_add()` | Create and attach a new virtual port |
| `ovs_dp_cmd_new()` | Netlink handler: create new datapath |
| `ovs_flow_cmd_new()` | Netlink handler: install new flow |
| `ovs_packet_cmd_execute()` | Netlink handler: execute actions on a packet |

---

## Key Source Files

| File | Purpose |
|---|---|
| `net/openvswitch/datapath.c` | Datapath core: process_packet, upcall, netlink |
| `net/openvswitch/flow.c` | Flow key extraction from packet headers |
| `net/openvswitch/flow_table.c` | Flow table: lookup, insert, delete |
| `net/openvswitch/actions.c` | Action execution: output, set, push/pop |
| `net/openvswitch/vport.c` | Virtual port abstraction |
| `net/openvswitch/vport-netdev.c` | Netdev vport implementation |
| `net/openvswitch/conntrack.c` | Connection tracking integration |
| `include/uapi/linux/openvswitch.h` | Netlink API definitions |

---

## Analogy

Open vSwitch kernel datapath is like a **smart highway interchange**:

- Packets arriving at a vport are like cars entering the interchange.
- The **flow table** is a set of routing signs: "cars with license plate
  matching pattern X, heading to destination Y → take exit 3."
- On a **flow hit**, the car is immediately routed (fast path) — no need
  to call the traffic controller.
- On a **flow miss**, the car is pulled over and the **traffic controller**
  (ovs-vswitchd) is radioed via the **upcall**: "I have a car I don't
  recognize." The controller decides the route, posts a new sign (installs
  a flow), and sends the car on its way.
- **Actions** are the maneuvers at the interchange: change lanes (modify
  headers), take a tunnel (VXLAN encap), add a toll tag (push VLAN),
  or loop back for re-evaluation (recirculate).

---

## References

- `net/openvswitch/` — kernel datapath
- `include/uapi/linux/openvswitch.h` — Netlink API
- Open vSwitch documentation: https://docs.openvswitch.org/
- OVS design doc: `Documentation/networking/openvswitch.rst`
