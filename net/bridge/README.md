# Linux Kernel Ethernet Bridge

## Overview

The **Linux bridge** implements a software **Layer 2 (Ethernet) switch** in the
kernel. It forwards frames between bridge ports based on MAC address learning,
implements the **Spanning Tree Protocol (STP/RSTP)** to prevent loops, supports
**VLAN-aware filtering**, and integrates with **netfilter bridge hooks**
(`br_netfilter`) for firewalling bridged traffic.

The bridge is widely used for VM networking (libvirt/QEMU), container networking
(Docker, LXC), and network namespaces.

Source: `net/bridge/`, `include/linux/if_bridge.h`, `include/uapi/linux/if_bridge.h`.

---

## Subsystem Stack

```
┌─────────────────────────────────────────────────────────────────┐
│                        USERSPACE                                │
│                                                                 │
│   ip link add br0 type bridge           bridge fdb show         │
│   ip link set eth0 master br0           bridge vlan add          │
│   brctl addbr / addif (legacy)          bridge monitor          │
│   iproute2 / NetworkManager / systemd-networkd                  │
└──────────────────────────────┬──────────────────────────────────┘
                               │  Netlink (RTM_NEWLINK, RTM_NEWNEIGH)
┌──────────────────────────────▼──────────────────────────────────┐
│                     BRIDGE DEVICE (br_device.c)                  │
│                                                                 │
│  struct net_bridge — master device representing the switch       │
│  br_dev_xmit() — transmit from bridge device (local traffic)    │
│  Owns: FDB table, STP state, VLAN database, multicast state     │
└──────────────────────────────┬──────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────┐
│                     FRAME HANDLING (br_input.c, br_forward.c)    │
│                                                                 │
│  br_handle_frame()                                              │
│    │                                                             │
│    ├─ Is it for us (local)?  → br_pass_frame_up() → netif_rx    │
│    │                                                             │
│    ├─ FDB lookup: known unicast?                                 │
│    │   ├─ YES → br_forward() to single port                     │
│    │   └─ NO  → br_flood() to all forwarding ports              │
│    │                                                             │
│    └─ Multicast? → br_multicast_rcv() → selective flood/snoop   │
│                                                                 │
│  VLAN filtering applied at ingress and egress                    │
└──────────────────────────────┬──────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────┐
│                     FDB — Forwarding Database (br_fdb.c)         │
│                                                                 │
│  struct net_bridge_fdb_entry                                     │
│    ├── MAC address → port mapping                                │
│    ├── Learned dynamically (ageing timer) or static              │
│    ├── VLAN-aware: per-VLAN FDB entries                          │
│    └── Offloadable to hardware (switchdev)                       │
│                                                                 │
│  br_fdb_update() — learn/refresh source MAC on ingress           │
│  br_fdb_find_rcu() — lookup destination MAC for forwarding       │
└──────────────────────────────┬──────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────┐
│                     STP — Spanning Tree (br_stp.c, br_stp_if.c)  │
│                                                                 │
│  State machine per port:                                         │
│                                                                 │
│  DISABLED ──► BLOCKING ──► LISTENING ──► LEARNING ──► FORWARDING │
│                  ▲                                       │       │
│                  └───────────────────────────────────────┘       │
│                         (topology change)                        │
│                                                                 │
│  br_stp_rcv() — process incoming BPDU frames                    │
│  br_stp_recalc_bridge_id() — recompute bridge ID on port change  │
│  Supports STP, RSTP, and user-mode STP (via mstpd)              │
└──────────────────────────────┬──────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────┐
│              VLAN FILTERING (br_vlan.c, br_vlan_options.c)       │
│                                                                 │
│  VLAN-aware bridge: per-port allowed VLAN set + PVID             │
│  Ingress: check VLAN membership, assign PVID if untagged         │
│  Egress: strip tag if PVID, pass tagged if not                   │
│  VLAN tunneling for Q-in-Q and EVPN                              │
└──────────────────────────────┬──────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────┐
│              NETFILTER BRIDGE (br_netfilter.c)                   │
│                                                                 │
│  br_nf_pre_routing / br_nf_forward / br_nf_post_routing          │
│  Allows iptables/nftables rules to inspect bridged traffic       │
│  Bridge-specific hooks: NF_BR_PRE_ROUTING, NF_BR_FORWARD, etc.  │
└──────────────────────────────┬──────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────┐
│                     BRIDGE PORTS (struct net_bridge_port)         │
│                                                                 │
│  Each enslaved interface (eth0, veth, etc.)                      │
│  Port state, STP role, VLAN config, hairpin mode                 │
│  br_port_carrier_check() — react to link state changes           │
└──────────────────────────────┬──────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────┐
│                   PHYSICAL / VIRTUAL INTERFACES                  │
│   eth0, eth1, veth pairs, tap devices, bond interfaces          │
└─────────────────────────────────────────────────────────────────┘
```

---

## Layer-by-Layer Explanation

### 1. Frame Reception (`br_input.c`)

When a frame arrives on a bridge port, the network stack calls
`br_handle_frame()` via the port's `rx_handler`. This is the main entry
point for all bridged traffic.

- **Local delivery**: If the destination MAC matches the bridge itself or is a
  multicast group the bridge has joined, `br_pass_frame_up()` delivers it to
  the local network stack.
- **Forwarding**: Otherwise, the FDB is consulted and the frame is either
  forwarded to a specific port or flooded.

### 2. Forwarding and Flooding (`br_forward.c`)

- **`br_forward()`**: Sends a frame out a single port (known unicast).
  Checks port STP state (must be FORWARDING), applies VLAN egress rules.
- **`br_flood()`**: Sends a frame out all ports in FORWARDING state except
  the ingress port (unknown unicast, broadcast, or unknown multicast).

### 3. FDB — Forwarding Database (`br_fdb.c`)

The FDB maps {MAC, VLAN} → port:

- **Dynamic learning**: `br_fdb_update()` is called on every ingress frame
  to learn or refresh the source MAC address.
- **Ageing**: Entries expire after `ageing_time` (default 300s).
- **Static entries**: Added via `bridge fdb add` — never age out.
- **FDB notifications**: Netlink `RTM_NEWNEIGH` / `RTM_DELNEIGH` for
  userspace and hardware offload (switchdev).

### 4. STP — Spanning Tree Protocol (`br_stp.c`)

Prevents broadcast storms in redundant topologies:

- **Port states**: DISABLED → BLOCKING → LISTENING → LEARNING → FORWARDING
- **BLOCKING**: Port receives BPDUs but does not forward or learn.
- **LISTENING**: Port participates in STP election but does not learn.
- **LEARNING**: Port learns MAC addresses but does not forward.
- **FORWARDING**: Full operation — learns and forwards.
- **`br_stp_rcv()`**: Processes incoming BPDU frames (bridge protocol data units).
- **`br_stp_recalc_bridge_id()`**: Recomputes bridge priority/ID when ports change.

### 5. VLAN-Aware Bridge (`br_vlan.c`)

When `vlan_filtering` is enabled:

- Each port has a set of allowed VLANs and a PVID (Port VLAN ID).
- Untagged ingress frames are assigned the PVID.
- Frames with disallowed VLAN tags are dropped.
- On egress, the PVID tag is stripped (untagged egress).

### 6. Netfilter Bridge Hooks (`br_netfilter.c`)

Enables iptables/nftables inspection of bridged (L2) traffic:

- `br_nf_pre_routing()`: Pre-routing hook for bridged frames.
- Allows IP-level filtering on traffic that never leaves Layer 2.
- Used heavily in container/VM networking for security groups.

---

## Key Data Structures

| Structure | Purpose |
|---|---|
| `struct net_bridge` | Bridge device: FDB, STP state, VLAN db, port list |
| `struct net_bridge_port` | Per-port: STP state/role, VLAN config, flags |
| `struct net_bridge_fdb_entry` | FDB entry: MAC, VLAN, port, flags, ageing |
| `struct net_bridge_vlan` | Per-port VLAN entry: VID, flags (PVID, untagged) |
| `struct net_bridge_mcast_port` | Multicast snooping state per port |
| `struct br_config_bpdu` | STP BPDU message fields |

## Key Functions

| Function | Purpose |
|---|---|
| `br_handle_frame()` | Main entry: classify and dispatch incoming frames |
| `br_forward()` | Forward frame to a single known port |
| `br_flood()` | Flood frame to all forwarding ports |
| `br_pass_frame_up()` | Deliver frame to local network stack |
| `br_fdb_update()` | Learn/refresh source MAC in FDB |
| `br_dev_xmit()` | Transmit from bridge device (locally originated) |
| `br_stp_rcv()` | Process STP BPDU frames |
| `br_stp_recalc_bridge_id()` | Recompute bridge ID on topology change |
| `br_nf_pre_routing()` | Netfilter pre-routing hook for bridged traffic |
| `br_port_carrier_check()` | Handle link up/down on bridge port |

## Key Source Files

| File | Purpose |
|---|---|
| `net/bridge/br.c` | Module init, notifier registration |
| `net/bridge/br_device.c` | Bridge net_device_ops (xmit, open, etc.) |
| `net/bridge/br_input.c` | Frame reception and classification |
| `net/bridge/br_forward.c` | Unicast forwarding and flooding |
| `net/bridge/br_fdb.c` | Forwarding database (MAC learning) |
| `net/bridge/br_stp.c` | STP state machine and BPDU processing |
| `net/bridge/br_stp_if.c` | STP interface (port state transitions) |
| `net/bridge/br_vlan.c` | VLAN filtering and PVID management |
| `net/bridge/br_netfilter_hooks.c` | Netfilter bridge integration |
| `net/bridge/br_multicast.c` | IGMP/MLD snooping |
| `include/linux/if_bridge.h` | Bridge data structures |

---

## Analogy

The Linux bridge is like a **smart office Ethernet switch**:

- **Bridge ports** are the physical switch ports — you plug cables (interfaces)
  into them.
- The **FDB** is the switch's MAC address table — it learns which device is
  behind which port by watching traffic.
- **Flooding** is what the switch does when it doesn't know a destination —
  it sends the frame out every port (like shouting in an office).
- **STP** is the loop-prevention protocol — if someone accidentally creates a
  cable loop, STP disables redundant paths to prevent a broadcast storm.
- **VLAN filtering** divides the switch into virtual segments — like putting
  different departments on separate logical networks even though they share
  the same physical switch.
- **br_netfilter** is like adding a security guard at the switch who can
  inspect packets even though they never leave Layer 2.

---

## References

- `include/linux/if_bridge.h` — Bridge structures
- `include/uapi/linux/if_bridge.h` — UAPI constants
- `Documentation/networking/bridge.rst`
- `net/bridge/` — Full implementation
- IEEE 802.1D (STP), IEEE 802.1Q (VLANs)
