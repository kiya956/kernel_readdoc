# 802.1Q VLAN Subsystem (`net/8021q/`)

## Overview

The Linux 802.1Q VLAN subsystem implements IEEE 802.1Q virtual LAN tagging.
It creates **virtual network interfaces** (e.g. `eth0.100`) on top of a real
("lower") device.  On **transmit** the driver inserts a 4-byte VLAN tag into
the Ethernet frame; on **receive** the tag is stripped and the frame is
delivered to the matching VLAN net-device.  This emulates **trunk ports**
(tagged traffic) and **access ports** (untagged traffic forwarded to a single
VLAN) entirely in software, though many NICs can offload the insert/strip to
hardware (NETIF_F_HW_VLAN_CTAG_TX / RX).

---

## Data-Path Diagram

```
 ┌─────────────────────────── TX PATH ───────────────────────────┐
 │                                                               │
 │  Userspace  ──►  socket / IP stack                            │
 │                       │                                       │
 │                       ▼                                       │
 │           ┌──────────────────────┐                            │
 │           │  vlan_dev_hard_      │   VLAN virtual net-device  │
 │           │  start_xmit()       │   (e.g. eth0.100)          │
 │           └─────────┬────────────┘                            │
 │                     │                                         │
 │                     ▼                                         │
 │        ┌─────────────────────────┐                            │
 │        │  Insert 802.1Q tag      │   4 bytes: TPID + TCI     │
 │        │  (or let HW offload)    │   TPID = 0x8100           │
 │        └─────────┬───────────────┘                            │
 │                  │                                            │
 │                  ▼                                            │
 │        ┌─────────────────────────┐                            │
 │        │  Real device xmit      │   dev_queue_xmit() on the  │
 │        │  (e.g. e1000 driver)   │   underlying NIC           │
 │        └─────────────────────────┘                            │
 └───────────────────────────────────────────────────────────────┘

 ┌─────────────────────────── RX PATH ───────────────────────────┐
 │                                                               │
 │        ┌─────────────────────────┐                            │
 │        │  NIC / NAPI poll        │   Frame arrives on wire    │
 │        └─────────┬───────────────┘                            │
 │                  │                                            │
 │                  ▼                                            │
 │        ┌─────────────────────────┐                            │
 │        │  vlan_skb_recv()        │   rx_handler registered    │
 │        │  (a.k.a. vlan_do_      │   on the real device       │
 │        │   receive_skb)         │                            │
 │        └─────────┬───────────────┘                            │
 │                  │                                            │
 │                  ▼                                            │
 │        ┌─────────────────────────┐                            │
 │        │  Strip 802.1Q tag       │   __vlan_hwaccel_pull_tag  │
 │        │  Look up vlan_group     │   or __vlan_get_tag        │
 │        └─────────┬───────────────┘                            │
 │                  │                                            │
 │                  ▼                                            │
 │        ┌─────────────────────────┐                            │
 │        │  Deliver to VLAN        │   netif_receive_skb() on   │
 │        │  net-device (eth0.100)  │   the VLAN interface       │
 │        └─────────────────────────┘                            │
 └───────────────────────────────────────────────────────────────┘
```

### 802.1Q Tag Format (4 bytes inserted after src MAC)

```
  0                   1                   2                   3
  0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
 +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
 |         TPID (0x8100)         | PCP |D|       VID (0-4095)    |
 +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+

   TPID  – Tag Protocol Identifier (0x8100 for 802.1Q)
   PCP   – Priority Code Point (3 bits, maps to CoS)
   DEI/D – Drop Eligible Indicator (1 bit)
   VID   – VLAN Identifier (12 bits → 4096 VLANs)
```

---

## Layer-by-Layer Explanation

### 1 — VLAN Device Creation

A VLAN device is a **stacked net-device** whose `real_dev` pointer references
the physical NIC.  Creation can happen via:

| Method | Command |
|--------|---------|
| iproute2 (netlink) | `ip link add link eth0 name eth0.100 type vlan id 100` |
| legacy ioctl | `vconfig add eth0 100` |

`register_vlan_dev()` allocates a `struct net_device`, wires up
`vlan_dev_priv`, and inserts the VID into the parent's `vlan_group` hash.
The VLAN device inherits the real device's MTU minus 4 bytes (tag overhead)
and most hardware feature flags.

### 2 — Tag Handling (TX / RX)

**TX** — `vlan_dev_hard_start_xmit()`:

1. If the NIC advertises `NETIF_F_HW_VLAN_CTAG_TX`, the tag is placed in
   `skb->vlan_tci` and the hardware inserts it on the wire.
2. Otherwise the function calls `__vlan_put_tag()` to push 4 bytes into the
   SKB headroom and writes TPID + TCI.

**RX** — `vlan_do_receive()` / `vlan_skb_recv()`:

1. The NIC (or `eth_type_trans`) recognises the 0x8100 EtherType and stores
   the TCI in `skb->vlan_tci`, setting `skb->vlan_present`.
2. `vlan_do_receive()` looks up the `vlan_group` on the real device, finds
   the matching VLAN net-device, and redirects the SKB to it.

### 3 — GVRP / MVRP (Dynamic VLAN Registration)

The subsystem optionally supports:

* **GVRP** (GARP VLAN Registration Protocol, IEEE 802.1D-2004) — advertises
  locally configured VLANs to the switch so trunks are auto-pruned.
* **MVRP** (Multiple VLAN Registration Protocol, IEEE 802.1ak) — replacement
  for GVRP using MRP as the underlying registration machinery.

Both are implemented as callbacks in `vlan_gvrp.c` / `vlan_mvrp.c` and are
toggled per-VLAN device via netlink flags.

---

## Key Structures

| Structure | Header / File | Purpose |
|-----------|---------------|---------|
| `struct vlan_dev_priv` | `include/linux/if_vlan.h` | Per-VLAN-device private data: `real_dev`, `vlan_id`, `vlan_proto`, egress/ingress priority maps, flags |
| `struct vlan_group` | `include/linux/if_vlan.h` | Hash table (4096 buckets) mapping VID → VLAN net-device, attached to the real device |
| `struct vlan_ethhdr` | `include/linux/if_vlan.h` | Extended Ethernet header with 802.1Q tag fields (`h_vlan_TCI`, `h_vlan_encapsulated_proto`) |
| `struct vlan_pcpu_stats` | `include/linux/if_vlan.h` | Per-CPU TX/RX packet and byte counters for a VLAN device |

---

## Key Functions

| Function | File | Role |
|----------|------|------|
| `register_vlan_dev()` | `vlan.c` | Register a new VLAN net-device with the networking stack and add VID to parent |
| `vlan_dev_hard_start_xmit()` | `vlan_dev.c` | TX handler — insert 802.1Q tag (SW or HW offload) and forward to real device |
| `vlan_skb_recv()` / `vlan_do_receive()` | `vlan_core.c` | RX handler — look up VID, strip tag, deliver SKB to VLAN net-device |
| `vlan_ioctl_handler()` | `vlan.c` | Process legacy `ADD_VLAN` / `DEL_VLAN` / `SET_VLAN` ioctls from `vconfig` |
| `vlan_newlink()` | `vlan_netlink.c` | Netlink `RTM_NEWLINK` handler for `ip link add … type vlan` |
| `vlan_vid_add()` | `vlan_core.c` | Add a VID filter to a device's VLAN group (called internally and by bridge) |
| `vlan_vid_del()` | `vlan_core.c` | Remove a VID filter from a device's VLAN group |
| `vlan_dev_open()` | `vlan_dev.c` | `ndo_open` — bring the VLAN interface up, register GVRP/MVRP if enabled |
| `vlan_dev_change_flags()` | `vlan_dev.c` | Propagate IFF_* flag changes between VLAN and real device |

---

## Common Operations

```bash
# Load the 8021q module (usually auto-loaded)
modprobe 8021q

# Create a VLAN interface (preferred, netlink-based)
ip link add link eth0 name eth0.100 type vlan id 100
ip addr add 10.0.100.1/24 dev eth0.100
ip link set eth0.100 up

# Create with legacy tool
vconfig add eth0 200

# Set egress priority mapping  (skb->priority 3 → CoS 5)
ip link set eth0.100 type vlan egress-qos-map 3:5

# Show VLAN info
cat /proc/net/vlan/config
ip -d link show eth0.100

# Delete
ip link del eth0.100
```

---

## Key Source Files

| File | Purpose |
|------|---------|
| `net/8021q/vlan.c` | Module init/exit, ioctl handler, `register_vlan_dev`, notifier |
| `net/8021q/vlan_dev.c` | VLAN net-device ops: xmit, open, stop, change_mtu, set_mac, ethtool |
| `net/8021q/vlan_core.c` | Core RX path (`vlan_do_receive`), `vlan_vid_add/del`, VLAN group management |
| `net/8021q/vlan_netlink.c` | Netlink (rtnl_link_ops) interface for creating/configuring VLANs |
| `net/8021q/vlan_gvrp.c` | GVRP protocol glue (join/leave VLAN announcements) |
| `net/8021q/vlan_mvrp.c` | MVRP protocol glue |
| `net/8021q/vlanproc.c` | `/proc/net/vlan/*` entries |
| `include/linux/if_vlan.h` | Public API, inline helpers (`vlan_dev_vlan_id`, `__vlan_put_tag`) |

---

## Analogy

Think of an 802.1Q VLAN tag like a **coloured wristband at a festival**.
Everyone walks through the same entrance gate (the physical NIC cable), but
the wristband colour (VID) determines which stage area (VLAN interface) you
are admitted to.  On the way **out**, the bouncer (TX path) slaps the correct
wristband onto your arm; on the way **in**, the scanner (RX path) reads the
wristband, routes you to the right area, and removes it so you can enjoy the
show without worrying about tagging logistics.

---

## References

1. IEEE 802.1Q-2022 — *Bridges and Bridged Networks*
2. `Documentation/networking/vlan.rst` in the kernel tree
3. `include/linux/if_vlan.h` — public VLAN API & inline helpers
4. `include/uapi/linux/if_vlan.h` — UAPI constants shared with userspace
5. iproute2 man page: `ip-link(8)`, section on `type vlan`
6. `man 8 vconfig` — legacy VLAN configuration tool
