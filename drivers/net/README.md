# Linux Network Driver Subsystem — Kernel Driver Analysis

> Kernel: noble-linux-oem / oem-6.17-next  
> Source: `drivers/net/`  networking core: `net/core/`

---

## 1. Full Subsystem Stack

```
┌──────────────────────────────────────────────────────────────────────┐
│                          User Space                                  │
│  application:  send() / recv() / sendmsg() / recvmsg()              │
│  tools:  ip / ethtool / tc / ss / netstat / iperf                   │
└────────────────────────────┬─────────────────────────────────────────┘
                             │ syscall
┌────────────────────────────▼─────────────────────────────────────────┐
│                      Socket Layer  (net/socket.c)                    │
│            SOCK_STREAM / SOCK_DGRAM / SOCK_RAW                       │
└────────────────────────────┬─────────────────────────────────────────┘
                             │
┌────────────────────────────▼─────────────────────────────────────────┐
│                    Protocol Stack  (net/ipv4,ipv6,…)                 │
│  TCP (tcp.c)  UDP (udp.c)  ICMP  SCTP  DCCP  raw_send_hdrinc        │
│  ip_output() / ip_rcv()  →  routing / netfilter / conntrack         │
└────────────────────────────┬─────────────────────────────────────────┘
                             │  sk_buff
┌────────────────────────────▼─────────────────────────────────────────┐
│             Network Core  (net/core/dev.c)                           │
│                                                                      │
│  TX path                           RX path                          │
│  dev_queue_xmit()                  netif_receive_skb()              │
│    │ qdisc (TC / pfifo_fast)         │ GRO (generic receive offload)│
│    │ dev_hard_start_xmit()           │ protocol demux               │
│    └──► ndo_start_xmit()            └──► ip_rcv() / arp_rcv() …    │
│                                                                      │
│  NAPI subsystem: softirq poll loop (napi_poll → driver poll())      │
│  Page Pool: DMA buffer recycling   XDP: early packet processing     │
└────────────────────────────┬─────────────────────────────────────────┘
                             │
       ┌─────────────────────┼──────────────────────────┐
       │                     │                          │
┌──────▼──────────┐  ┌───────▼──────────┐  ┌───────────▼─────────────┐
│  Ethernet NIC   │  │  Virtual devices │  │  Wireless (mac80211)    │
│  Drivers        │  │                  │  │                         │
│  drivers/net/   │  │  bonding/        │  │  drivers/net/wireless/  │
│  ethernet/      │  │  team/           │  │  net/mac80211/          │
│                 │  │  macvlan.c       │  │  drivers/net/wireless/  │
│  Intel: ice/    │  │  ipvlan/         │  │    intel/iwlwifi        │
│         igb/    │  │  veth.c          │  │    realtek/rtw88        │
│         e1000e/ │  │  tun.c           │  │    broadcom/brcmfmac    │
│  Realtek: r8169 │  │  dummy.c         │  │                         │
│  Broadcom:bnx2x │  │  loopback.c      │  │                         │
│  Marvell:mvneta │  │  netkit.c        │  │                         │
└──────┬──────────┘  └──────────────────┘  └─────────────────────────┘
       │
┌──────▼──────────────────────────────────────────────────────────────┐
│          PHY Layer  (drivers/net/phy/)                              │
│  MDIO bus  →  phylib (phy_device)  →  SFP / SERDES / MAC           │
│  phy_start() / phy_stop() / phy_connect()                           │
└──────▼──────────────────────────────────────────────────────────────┘
       │
┌──────▼──────────────────────────────────────────────────────────────┐
│   Hardware:  NIC ASIC  (PCI/PCIe device)                            │
│   DMA rings (TX/RX descriptor rings)  →  memory  ↔  wire           │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 2. The Packet Buffer — `struct sk_buff`

The **sk_buff** (socket buffer) is to networking what `struct urb` is to USB and `struct bio` is to block I/O — the universal packet container.

```
          sk_buff memory layout
  ┌──────────────────────────────┐
  │  sk_buff (metadata / header) │
  │   len, data_len, protocol    │
  │   dev, sk, tstamp, mark      │
  │   cb[48] (per-layer scratch) │
  │   _nfct, skb_iif             │
  └──────────────┬───────────────┘
                 │ points into ↓
  ┌──────────────▼───────────────────────────────────┐
  │  data buffer (skb->head … skb->end)              │
  │                                                  │
  │  [headroom] [Ethernet][IP][TCP][payload][tailroom]│
  │              ▲         ▲    ▲    ▲                │
  │         skb->mac_header  network  transport       │
  │                    skb->data                      │
  └──────────────────────────────────────────────────┘
```

| Field | Meaning |
|-------|---------|
| `len` | total data length (linear + fragments) |
| `data_len` | non-linear (paged) portion |
| `protocol` | ETH_P_IP / ETH_P_IPV6 / ETH_P_ARP … |
| `dev` | originating/destination `net_device` |
| `sk` | owning socket (NULL for forwarded packets) |
| `cb[48]` | per-layer private data (TCP uses it for sequence numbers) |
| `_nfct` | netfilter conntrack pointer |
| `skb_shinfo` | frags[], GSO/GRO metadata, TX timestamp |

---

## 3. `net_device` and `net_device_ops`

```c
struct net_device {
    char         name[IFNAMSIZ];      // "eth0", "ens3", "wlan0"
    unsigned int flags;               // IFF_UP, IFF_PROMISC …
    unsigned int mtu;                 // default 1500
    const struct net_device_ops *netdev_ops;
    const struct ethtool_ops    *ethtool_ops;
    struct netdev_queue  *_tx;        // TX queue array (multi-queue)
    struct netdev_rx_queue *_rx;      // RX queue array
    struct napi_struct   napi_list;   // NAPI instances
    // stats, features, offloads, XDP prog, …
};

struct net_device_ops {        // driver vtable
    int  (*ndo_open)(dev);           // ip link set up
    int  (*ndo_stop)(dev);           // ip link set down
    tx_t (*ndo_start_xmit)(skb, dev);// send packet
    void (*ndo_set_rx_mode)(dev);    // promiscuous / multicast
    int  (*ndo_change_mtu)(dev, mtu);
    void (*ndo_tx_timeout)(dev, txq);
    void (*ndo_get_stats64)(dev, stats);
    int  (*ndo_set_features)(dev, features);
    // SR-IOV, TC offload, XDP, …
};
```

---

## 4. Layer-by-Layer Explanation

### 4.1 Hardware — DMA Descriptor Rings
- **TX ring**: driver writes descriptors (DMA address + length); NIC reads them, sends frame, raises TX completion interrupt.  
- **RX ring**: driver pre-allocates buffers, writes DMA addresses; NIC fills them with received frames, raises RX interrupt.  
- Modern NICs (Intel ICE, Mellanox ConnectX): multiple TX/RX queue pairs for RSS (Receive Side Scaling) across CPUs.

### 4.2 PHY Layer (`drivers/net/phy/`)
- **phylib**: abstraction for 10/100/1G/10G/25G+ PHY chips.  
- `phy_device` models one PHY; `mii_bus` / `mdio_bus` for management bus.  
- `phy_connect(netdev, phy_id, handler, intf)` links PHY to MAC.  
- Auto-negotiation state machine: `phy_start_aneg()` → polls link status.  
- SFP/SFPP modules: `drivers/net/phy/sfp.c` handles cage/cage-less optical modules.

### 4.3 Ethernet NIC Drivers (`drivers/net/ethernet/`)
Each driver implements:
1. PCI probe → `alloc_etherdev_mqs()` → fill `net_device_ops` → `register_netdev()`
2. `ndo_open`: enable DMA rings, request IRQ, start PHY, enable NAPI
3. ISR: `napi_schedule()` → defers processing to softirq
4. `poll()`: drain RX ring, call `napi_gro_receive()` or `netif_receive_skb()`
5. `ndo_start_xmit`: fill TX descriptor, ring doorbell

Notable driver families:

| Family | Chips | Driver |
|--------|-------|--------|
| Intel ICE | E810 (100GbE), E822/E823 | `ice/` |
| Intel igb | I350, I210 (1GbE) | `igb/` |
| Intel e1000e | 82574/82579 (1GbE laptop) | `e1000e/` |
| Intel igc | I225/I226 (2.5GbE) | `igc/` |
| Realtek | RTL8169 (1GbE), RTL8125 (2.5GbE) | `r8169.c` |
| Broadcom | BCM57xx (bnx2x), BCM5720 (tg3) | `bnx2x/`, `tg3.c` |
| Marvell | Armada (mvneta), OcteonTX | `mvneta/` |

### 4.4 NAPI (New API — Receive Polling)
The key RX design pattern eliminating interrupt storms:

```
High-traffic RX:
  1. First frame → NIC raises IRQ
  2. ISR: mask NIC RX IRQ, call napi_schedule()
  3. NET_RX_SOFTIRQ fires → net_rx_action()
  4. driver->poll(): drain up to budget frames
     napi_gro_receive() per frame
  5. If ring drained: napi_complete_done() → re-enable IRQ
     If budget exhausted: yield, re-schedule (keeps CPU yielding)
```

`napi_struct.weight` = 64 (default budget). Prevents live-lock under flood.

### 4.5 GRO — Generic Receive Offload (`net/core/gro.c`)
- Coalesces multiple TCP/UDP segments into a single large `sk_buff` before handing to IP.  
- Saves per-packet overhead in the protocol stack.  
- `napi_gro_receive()` → `dev_gro_receive()` → `tcp4_gro_receive()`.

### 4.6 TX Path and qdiscs (Traffic Control)
```
sk_buff from TCP/IP
  │
  ▼ dev_queue_xmit()
  │  pick txq by CPU / sk hash
  │
  ▼ qdisc enqueue  (e.g., pfifo_fast, fq, htb, cake)
  │
  ▼ qdisc dequeue → dev_hard_start_xmit()
  │
  ▼ ndo_start_xmit(skb, dev)
  │  fill TX descriptor ring
  │  ring doorbell (MMIO write)
  │
  ▼ NIC DMA → wire
  └─► TX completion IRQ → skb_free()
```

`tc qdisc add dev eth0 root fq` installs the Fair Queue discipline.

### 4.7 XDP — eXpress Data Path
- BPF program attached at driver RX, **before** sk_buff allocation.  
- Possible verdicts: `XDP_PASS`, `XDP_DROP`, `XDP_TX`, `XDP_REDIRECT`.  
- AF_XDP zero-copy socket: frames land directly in user-space UMEM ring.  
- Supported in ICE, igb, mlx5, ixgbe, virtio-net, etc.

### 4.8 Virtual Devices
| Driver | Purpose |
|--------|---------|
| `loopback.c` | lo — local loopback |
| `veth.c` | Virtual Ethernet pair (container networking) |
| `tun.c` | TUN (L3) / TAP (L2) — VPN, QEMU |
| `bonding/` | Link aggregation (active-backup, LACP) |
| `macvlan.c` | Multiple MACs on one physical interface |
| `ipvlan/` | Multiple IPs on one MAC |
| `dummy.c` | Black-hole interface for testing |
| `netkit.c` | BPF-programmable virtual NIC (6.7+) |

### 4.9 Wireless (`drivers/net/wireless/`, `net/mac80211/`)
- **mac80211**: 802.11 frame management, association, rate control, A-MPDU.  
- **cfg80211**: nl80211 user-space API (iw, wpa_supplicant).  
- Driver families: Intel `iwlwifi` (Wi-Fi 6/7), Realtek `rtw88`/`rtw89`, Broadcom `brcmfmac`.

### 4.10 ethtool (`net/ethtool/`)
- `ethtool -i eth0` → `get_drvinfo`.  
- `ethtool -k eth0` → `get_features` (TSO, GSO, GRO, LRO, checksum offload).  
- `ethtool -g eth0` → `get_ringparam` (RX/TX ring sizes).  
- `ethtool -S eth0` → `get_ethtool_stats` (driver private counters).

---

## 5. Data-Flow Diagram — RX Path (NAPI)

```
Frame arrives at NIC
       │
NIC DMA → fills RX ring buffer (page pool buffer)
NIC raises MSI-X interrupt → NIC ISR (e.g., ice_misc_intr)
       │
       ▼
napi_schedule(&q_vector->napi)
  ─ masks interrupt source
  ─ raises NET_RX_SOFTIRQ
       │
       ▼  (softirq context, non-preemptible)
net_rx_action()
  for each NAPI: napi->poll(napi, budget)
       │
       ▼  (driver poll, e.g., ice_napi_poll)
  while frames in RX ring:
    build sk_buff (or use xdp_buff for XDP)
    if XDP prog: run bpf_prog → XDP_PASS/DROP/TX
    napi_gro_receive(napi, skb)
      GRO coalesce → napi_gro_flush()
       │
       ▼
netif_receive_skb(skb)
  __netif_receive_skb_core()
    deliver to ptype_all sniffers (tcpdump / AF_PACKET)
    vlan / bridge / macvlan processing
    deliver to ptype_base[ETH_P_IP] → ip_rcv()
       │
       ▼
ip_rcv() → ip_rcv_finish() → routing → local deliver
tcp_v4_rcv() → sk receive queue → user recv()
```

---

## 6. Data-Flow Diagram — TX Path

```
Application: send(sock, buf, len)
       │
       ▼
tcp_sendmsg() → sk_stream_alloc_skb()
  fills sk_buff with data, sets TSO/GSO segcount
       │
       ▼
tcp_write_xmit() → ip_queue_xmit() → ip_output()
  Netfilter OUTPUT hook (iptables / nftables)
       │
       ▼
dev_queue_xmit(skb)
  netdev_pick_tx() → select txq by CPU
  qdisc_run(): enqueue → dequeue
       │
       ▼
dev_hard_start_xmit(skb, dev, txq)
  ndo_start_xmit(skb, dev)    ← driver
    map skb → DMA address
    fill TX descriptor
    write doorbell register (MMIO)
       │
       ▼
NIC transmits frame on wire
NIC TX completion interrupt
  free tx descriptors + skb_free_datagram()
```

---

## 7. Key Data Structures Summary

```c
struct net_device  // NIC abstraction, one per interface
struct net_device_ops  // driver vtable (ndo_*)
struct sk_buff     // packet buffer + metadata
struct napi_struct // NAPI poll state per queue-vector
struct netdev_queue // per-TX-queue state + qdisc pointer
struct Qdisc       // traffic control discipline
struct phy_device  // PHY chip abstraction
struct ethtool_ops // ethtool vtable
```

---

## 8. Important Source Files

| File | Role |
|------|------|
| `net/core/dev.c` | Central dispatch: dev_queue_xmit, netif_receive_skb |
| `net/core/skbuff.c` | sk_buff alloc, clone, copy, free |
| `net/core/gro.c` | Generic Receive Offload |
| `net/core/filter.c` | BPF/XDP integration |
| `net/core/page_pool.c` | DMA buffer recycling pool |
| `drivers/net/ethernet/intel/ice/` | Intel E810 100GbE driver |
| `drivers/net/ethernet/intel/igb/` | Intel I350/I210 1GbE driver |
| `drivers/net/ethernet/realtek/r8169.c` | Realtek RTL8169/8125 |
| `drivers/net/phy/phy.c` | phylib state machine |
| `drivers/net/phy/sfp.c` | SFP/SFPP optical module support |
| `drivers/net/tun.c` | TUN/TAP virtual interface |
| `drivers/net/bonding/bond_main.c` | Link aggregation |
| `net/mac80211/` | 802.11 MAC sublayer |
| `net/ethtool/` | ethtool netlink API |

---

## 9. bpftrace / Python Test Case

See [`test_net_workflow.py`](test_net_workflow.py) in this directory.
