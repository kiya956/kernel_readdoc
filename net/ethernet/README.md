# Ethernet Subsystem — Protocol Handler & Frame Operations

## Overview

The **Ethernet subsystem** (`net/ethernet/eth.c`) implements the foundational
protocol handler for IEEE 802.3 / Ethernet II frames.  Its central job is
**protocol demuxing**: reading the EtherType from every incoming frame and
setting `skb->protocol` so the networking stack can hand the packet to the
correct upper-layer handler (IPv4, IPv6, ARP, …).

Key responsibilities:

| Area | What it does |
|------|-------------|
| **Protocol translation** | `eth_type_trans()` — reads the 2-byte EtherType / 802.2 LLC header and writes `skb->protocol` |
| **Header build / parse** | `eth_header()` builds a 14-byte Ethernet header; `eth_header_parse()` extracts source MAC |
| **Device setup** | `ether_setup()` initialises a `struct net_device` with Ethernet defaults (MTU 1500, broadcast FF:…, header_ops) |
| **MAC management** | `eth_mac_addr()` sets the hardware address; `eth_validate_addr()` rejects multicast / zero MACs |
| **GRO** | `eth_gro_receive()` / `eth_gro_complete()` participate in Generic Receive Offload for inner Ethernet headers |

---

## Packet Receive Path — Where eth_type_trans Fits

```
 ┌──────────────┐
 │  NIC driver  │  e.g. e1000e, igb, mlx5
 │  (NAPI poll) │
 └──────┬───────┘
        │  skb filled with raw frame
        ▼
 ┌──────────────────┐
 │ napi_gro_receive  │   or  netif_receive_skb
 └──────┬───────────┘
        │
        ▼
 ┌─────────────────────────────────────────────────────┐
 │              eth_type_trans(skb, dev)                │
 │                                                     │
 │  1. Pull 14-byte Ethernet header (skb->data += 14)  │
 │  2. Copy dest MAC → skb->pkt_type                   │
 │     • unicast-to-us → PACKET_HOST                   │
 │     • broadcast     → PACKET_BROADCAST              │
 │     • multicast     → PACKET_MULTICAST              │
 │     • other unicast → PACKET_OTHERHOST              │
 │  3. Read h_proto (EtherType)                        │
 │     • ≥ 0x0600  → Ethernet II  → return h_proto     │
 │     • < 0x0600  → 802.2 LLC    → htons(ETH_P_802_2) │
 │  4. skb->protocol = return value                    │
 └──────────┬──────────────────────────────────────────┘
            │
            ▼
 ┌────────────────────────┐
 │ Protocol handler        │
 │ dispatch via            │
 │ ptype_base[] hash table │
 │                         │
 │  ETH_P_IP   → ip_rcv   │
 │  ETH_P_IPV6 → ipv6_rcv │
 │  ETH_P_ARP  → arp_rcv  │
 │  …                      │
 └────────────────────────┘
```

Every NIC driver calls `eth_type_trans()` **before** handing the skb to the
stack.  Without this call, `skb->protocol` is unset and the packet is silently
dropped or mis-routed.

---

## Layer-by-Layer Explanation

### 1. Header Operations (`eth_header`, `eth_header_parse`, `eth_header_cache`)

When the IP layer (or any upper protocol) wants to **transmit**, it calls
`dev_hard_header()`, which dispatches to `eth_header()` via `dev->header_ops`.

```
 ip_output
   └─► dev_hard_header(skb, dev, ETH_P_IP, daddr, saddr, len)
         └─► dev->header_ops->create  →  eth_header()
               ├── writes  dst MAC   (6 bytes)
               ├── writes  src MAC   (6 bytes)
               └── writes  EtherType (2 bytes, network order)
```

`eth_header_parse()` does the inverse — given an skb, it copies out the
source MAC address (used by the neighbour subsystem to learn remote MACs).

`eth_header_cache()` stores a pre-built header template in `struct neighbour`
so that subsequent packets to the same destination skip the per-packet header
construction.

### 2. Type Translation (`eth_type_trans`)

The heart of the subsystem.  Called once per received frame:

1. **Advances `skb->data`** past the 14-byte Ethernet header via `skb_pull_inline()`.
2. **Classifies the destination MAC** to set `skb->pkt_type`:
   - If the dest MAC matches `dev->dev_addr` → `PACKET_HOST`
   - If the dest MAC is `ff:ff:ff:ff:ff:ff` → `PACKET_BROADCAST`
   - If bit 0 of the first byte is set → `PACKET_MULTICAST`
   - Otherwise → `PACKET_OTHERHOST`
3. **Reads the EtherType** (`eth->h_proto`):
   - Values ≥ `ETH_P_802_3_MIN` (0x0600) are Ethernet II → returned directly.
   - Smaller values indicate an 802.2 LLC frame → returns `htons(ETH_P_802_2)`.
   - Special-case: 802.2 + SNAP with an OUI of 0x000000 → extracts the real
     EtherType from the SNAP header.

### 3. Device Setup (`ether_setup`)

Called from `alloc_etherdev_mqs()` (and its wrappers) to initialise a
`struct net_device` with Ethernet defaults:

| Field | Value |
|-------|-------|
| `header_ops` | `&eth_header_ops` |
| `type` | `ARPHRD_ETHER` |
| `hard_header_len` | `ETH_HLEN` (14) |
| `min_header_len` | `ETH_HLEN` |
| `mtu` | `ETH_DATA_LEN` (1500) |
| `min_mtu` | `ETH_MIN_MTU` (68) |
| `max_mtu` | `ETH_MAX_MTU` (65535) |
| `addr_len` | `ETH_ALEN` (6) |
| `broadcast` | `ff:ff:ff:ff:ff:ff` |

### 4. MAC Address Management

- **`eth_mac_addr(dev, addr)`** — validates, then copies 6 bytes into
  `dev->dev_addr`.  Refuses if the device is running (`IFF_UP`).
- **`eth_validate_addr(dev)`** — returns `-EADDRNOTAVAIL` if the current
  `dev->dev_addr` is multicast or all-zeros (via `is_valid_ether_addr()`).

---

## Key Functions

| Function | File | Purpose |
|----------|------|---------|
| `eth_type_trans()` | `net/ethernet/eth.c` | **Critical path** — determines `skb->protocol` and `skb->pkt_type` for every received Ethernet frame |
| `eth_header()` | `net/ethernet/eth.c` | Builds a 14-byte Ethernet header (dst MAC + src MAC + EtherType) |
| `ether_setup()` | `net/ethernet/eth.c` | Initialises `net_device` with Ethernet defaults (MTU, header_ops, addr_len …) |
| `eth_mac_addr()` | `net/ethernet/eth.c` | Sets the MAC address on a net_device |
| `eth_validate_addr()` | `net/ethernet/eth.c` | Rejects invalid (multicast / zero) MAC addresses |
| `eth_header_parse()` | `net/ethernet/eth.c` | Extracts source MAC from an Ethernet header |
| `eth_header_cache()` | `net/ethernet/eth.c` | Caches a pre-built header in `struct neighbour` for fast TX |
| `eth_get_headlen()` | `net/ethernet/eth.c` | Computes network-header length via flow dissector |
| `eth_gro_receive()` | `net/ethernet/eth.c` | GRO callback — aggregates Ethernet-encapsulated frames |
| `eth_gro_complete()` | `net/ethernet/eth.c` | Finalises a GRO-merged Ethernet frame |
| `is_valid_ether_addr()` | `include/linux/etherdevice.h` | Inline helper — true if MAC is non-zero, non-multicast |
| `ether_addr_copy()` | `include/linux/etherdevice.h` | Fast 6-byte MAC copy |
| `ether_addr_equal()` | `include/linux/etherdevice.h` | Compare two MAC addresses |
| `eth_random_addr()` | `include/linux/etherdevice.h` | Generate a random locally-administered MAC |

---

## Key Structures

### `struct ethhdr`  (`include/uapi/linux/if_ether.h`)

```c
struct ethhdr {
    unsigned char   h_dest[ETH_ALEN];   /* destination MAC (6 bytes) */
    unsigned char   h_source[ETH_ALEN]; /* source MAC      (6 bytes) */
    __be16          h_proto;            /* EtherType / length field   */
} __attribute__((packed));
```

Total size: **14 bytes** (`ETH_HLEN`).

### Important constants (`include/uapi/linux/if_ether.h`)

| Constant | Value | Meaning |
|----------|-------|---------|
| `ETH_ALEN` | 6 | MAC address length |
| `ETH_HLEN` | 14 | Ethernet header length |
| `ETH_DATA_LEN` | 1500 | Max payload (standard MTU) |
| `ETH_P_IP` | 0x0800 | IPv4 |
| `ETH_P_IPV6` | 0x86DD | IPv6 |
| `ETH_P_ARP` | 0x0806 | ARP |
| `ETH_P_8021Q` | 0x8100 | 802.1Q VLAN |
| `ETH_P_802_3_MIN` | 0x0600 | Minimum EtherType value (below = 802.2 length) |

---

## Common Operations

### Allocating an Ethernet net_device (driver init)

```c
struct net_device *dev = alloc_etherdev(sizeof(struct my_priv));
/* → internally calls ether_setup() to set Ethernet defaults */
```

### Receiving a frame (driver NAPI poll)

```c
skb->protocol = eth_type_trans(skb, netdev);
napi_gro_receive(&priv->napi, skb);
```

### Building a header (TX path, called automatically)

```c
/* Kernel calls dev->header_ops->create → eth_header() */
dev_hard_header(skb, dev, ETH_P_IP, dest_mac, src_mac, skb->len);
```

### Setting a MAC address (ethtool / ifconfig)

```c
/* .ndo_set_mac_address = eth_mac_addr  — use the default */
```

---

## Key Source Files

| File | Role |
|------|------|
| `net/ethernet/eth.c` | **Main file** — all functions listed above |
| `include/linux/etherdevice.h` | Inline helpers: `is_valid_ether_addr`, `ether_addr_copy`, `eth_random_addr`, etc. |
| `include/uapi/linux/if_ether.h` | `struct ethhdr`, EtherType constants, `ETH_ALEN`, `ETH_HLEN` |
| `include/linux/netdevice.h` | `struct header_ops`, `struct net_device` (uses `eth_header_ops`) |
| `net/core/dev.c` | Calls `eth_type_trans()` indirectly through driver → NAPI path |

---

## Analogy

Think of `eth_type_trans()` as the **mail room clerk** in a large office
building.  Every envelope (Ethernet frame) that arrives has a destination
address (MAC) and a department code (EtherType).  The clerk:

1. **Opens the outer envelope** (pulls the 14-byte header off `skb->data`).
2. **Checks the address label** — is it for this building?  Broadcast?
   Someone else's mail? — and stamps the envelope accordingly (`skb->pkt_type`).
3. **Reads the department code** (EtherType) and **routes the letter** to the
   correct department (`skb->protocol` → IPv4, IPv6, ARP handler).

`eth_header()` is the reverse — the **outgoing mail desk** that writes the
destination address, return address, and department code onto a fresh envelope
before it goes out.

---

## References

- `net/ethernet/eth.c` — kernel source (the single-file subsystem)
- `include/linux/etherdevice.h` — inline helpers and MAC utilities
- `include/uapi/linux/if_ether.h` — `struct ethhdr` and protocol constants
- [Understanding Linux Network Internals, Ch. 13 — Protocol Handlers](https://www.oreilly.com/library/view/understanding-linux-network/0596002556/)
- IEEE 802.3 — Ethernet standard
- `Documentation/networking/` — kernel networking docs
