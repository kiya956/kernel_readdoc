# batman-adv — Better Approach To Mobile Adhoc Networking (Advanced)

## Overview

**B.A.T.M.A.N. Advanced (batman-adv)** is a Linux kernel mesh networking
driver that operates at **Layer 2** (Ethernet).  Unlike traditional routing
protocols (OSPF, BGP, babel) that work at the IP layer, batman-adv makes a
collection of wireless (or wired) nodes look like a single virtual Ethernet
switch — all nodes appear link-local to each other.

Key features:
- **Layer 2 mesh**: Ethernet frames are bridged through the mesh transparently
- **Two routing algorithms**: BATMAN IV (OGM-based) and BATMAN V (ELP+OGM2)
- **Distributed ARP Table (DAT)**: ARP snooping + distributed hash table
- **Bridge Loop Avoidance (BLA)**: detects and breaks Layer 2 loops
- **Gateway support**: announces internet gateways, auto-selects best one
- **Network coding**: XOR coding of packets to improve link utilization
- **Multicast optimization**: efficient mesh-wide multicast forwarding
- **Fragmentation**: splits oversized frames across the mesh
- **TP meter**: in-kernel throughput measurement tool

Source: `net/batman-adv/`.

---

## Subsystem Stack

```
┌────────────────────────────────────────────────────────────────┐
│                        USERSPACE                               │
│  batctl  iproute2 (ip link add bat0 type batadv)               │
│  batman_adv Generic Netlink family                             │
└──────────────────────────────┬─────────────────────────────────┘
                               │ Netlink / ioctl
┌──────────────────────────────▼─────────────────────────────────┐
│           MANAGEMENT / NETLINK  (netlink.c, sysfs.c)           │
│                                                                 │
│  Generic Netlink cmds: mesh info, originator table,            │
│  translation table, gateway list, interface list, stats        │
│  sysfs: /sys/class/net/bat0/mesh/  (routing algo, gw mode …)  │
└──────────────────────────────┬─────────────────────────────────┘
                               │
┌──────────────────────────────▼─────────────────────────────────┐
│     MESH INTERFACE (bat0)   mesh-interface.c                   │
│                                                                 │
│  Virtual netdev: batadv_interface_ops                          │
│  Rx: incoming frames classified and forwarded to sub-systems   │
│  Tx: frames from upper layer → select best nexthop → transmit  │
└──────────────────────────────┬─────────────────────────────────┘
              ┌────────────────┼─────────────────┐
              │                │                 │
┌─────────────▼──┐  ┌──────────▼───────┐ ┌──────▼──────────────┐
│ ROUTING ALGO   │  │ TRANSLATION TABLE│ │  SPECIAL FEATURES    │
│                │  │ (tt_*.c)         │ │                      │
│ BATMAN IV:     │  │                  │ │ BLA (bridge loop     │
│  bat_iv_ogm.c  │  │ Local TT:        │ │  avoidance)          │
│  OGM v1 flood  │  │  every client    │ │  bridge_loop_        │
│  → neighbor    │  │  behind bat0     │ │   avoidance.c        │
│  quality metric│  │ Global TT:       │ │                      │
│                │  │  mesh-wide ARP   │ │ DAT (distributed ARP │
│ BATMAN V:      │  │  resolution      │ │  table)              │
│  bat_v.c       │  │                  │ │  distributed-arp-    │
│  ELP probes    │  │ CRC-based sync   │ │   table.c            │
│  OGM2 flood    │  │ of global TT     │ │                      │
│  → throughput  │  │                  │ │ Multicast            │
│  metric        │  │                  │ │  multicast.c         │
└─────────────┬──┘  └──────────┬───────┘ └──────┬──────────────┘
              └────────────────┴────────────────┘
                               │
┌──────────────────────────────▼─────────────────────────────────┐
│            ORIGINATOR TABLE  (originator.c)                    │
│                                                                 │
│  struct batadv_orig_node per mesh node:                        │
│   • best nexthop / router                                      │
│   • per-neighbor link quality                                  │
│   • last-seen timestamp                                        │
└──────────────────────────────┬─────────────────────────────────┘
                               │
┌──────────────────────────────▼─────────────────────────────────┐
│         HARD INTERFACES  (hard-interface.c)                    │
│                                                                 │
│  Real interfaces enslaved to bat0 (e.g., eth0, wlan0)         │
│  Packet injection: batadv_send_skb_packet()                   │
│  Packet rx hook: batadv_batman_skb_recv()                     │
└──────────────────────────────┬─────────────────────────────────┘
                               │  raw Ethernet frames
┌──────────────────────────────▼─────────────────────────────────┐
│            PHYSICAL INTERFACES  (eth0, wlan0, …)               │
└────────────────────────────────────────────────────────────────┘
```

---

## Routing Algorithms

### BATMAN IV (OGM v1)
- Each node periodically floods **OGM (Originator Message)** packets
- Neighbors re-broadcast OGMs with reduced TTL
- Link quality calculated from the ratio of received vs expected OGMs
- Best path = highest **TQ (Transmission Quality)** value

### BATMAN V (OGM2 + ELP)
- **ELP (Echo Location Protocol)**: unicast probes measure per-link throughput
- **OGM2**: flooded with cumulative throughput metric; lower overhead than OGM
- Best path = highest minimum-throughput along the path

---

## Translation Table (TT)

Maps Ethernet MAC addresses of clients behind the mesh to their home batman-adv
nodes.  Two copies:
- **Local TT**: clients directly connected to this node
- **Global TT**: full mesh-wide ARP directory, synchronized using CRC checksums

When a frame arrives for a MAC, the TT lookup finds which mesh node to forward
it to.

---

## Key Data Structures

| Structure | Purpose |
|---|---|
| `batadv_priv` | Per-bat0 private data (algo, TT, BLA, originator table) |
| `batadv_orig_node` | One entry in the originator table per mesh peer |
| `batadv_neigh_node` | Per-neighbor state (link quality, last contact) |
| `batadv_hard_iface` | One enslaved interface (eth0, wlan0, …) |
| `batadv_tt_local_entry` | Local TT entry (MAC → bat0) |
| `batadv_tt_global_entry` | Global TT entry (MAC → remote node) |
| `batadv_ogm_packet` | Wire format of a BATMAN IV OGM |
| `batadv_ogm2_packet` | Wire format of a BATMAN V OGM2 |

---

## Key Source Files

| File | Purpose |
|---|---|
| `net/batman-adv/main.c` | Module init, recv handler table |
| `net/batman-adv/bat_iv_ogm.c` | BATMAN IV OGM processing |
| `net/batman-adv/bat_v.c` | BATMAN V core |
| `net/batman-adv/bat_v_elp.c` | ELP probe sending/processing |
| `net/batman-adv/bat_v_ogm.c` | OGM2 flood logic |
| `net/batman-adv/routing.c` | Frame forwarding decision |
| `net/batman-adv/originator.c` | Originator table management |
| `net/batman-adv/translation-table.c` | TT local + global |
| `net/batman-adv/hard-interface.c` | Interface management |
| `net/batman-adv/mesh-interface.c` | bat0 virtual netdev |
| `net/batman-adv/distributed-arp-table.c` | DAT ARP offload |
| `net/batman-adv/bridge_loop_avoidance.c` | BLA |
| `net/batman-adv/netlink.c` | Generic Netlink management |

---

## Analogy

batman-adv is like a **smart postal service that figures out routes on its own**:

- Each **node** is a post office that regularly tells its neighbors "I'm here,
  and here's how good my connections are" (OGM flooding).
- The **originator table** is each post office's routing book: for each
  destination city, which road should I take?
- The **translation table** is the address book: which city does each person
  (MAC address) live in?
- **BATMAN IV** counts how many letters arrive correctly (TQ metric).
  **BATMAN V** measures how fast letters can travel (throughput metric).
- The **hard interfaces** are the physical trucks (eth0, wlan0) that actually
  carry the letters between adjacent post offices.

---

## References

- `Documentation/networking/batman-adv.rst`
- `net/batman-adv/` — full implementation
- https://www.open-mesh.org/projects/batman-adv/wiki
- `include/uapi/linux/batadv_packet.h` — wire format
