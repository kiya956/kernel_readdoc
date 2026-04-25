# net/core — Linux Network Core Subsystem

## Overview

`net/core/` is the **heart of the Linux networking stack**. It provides the
fundamental infrastructure that every protocol family (IPv4, IPv6, Unix, etc.)
and every network device driver depends on:

- **Socket layer** (`sock.c`) — the kernel representation of sockets, reference
  counting, memory accounting, socket options.
- **sk_buff management** (`skbuff.c`) — the universal network packet buffer
  (`struct sk_buff`) used by every protocol.
- **Device registration & I/O** (`dev.c`) — `register_netdev()`,
  `netif_receive_skb()`, `dev_queue_xmit()`, NAPI polling.
- **Neighbour subsystem** (`neighbour.c`) — ARP/NDP generic neighbour cache.
- **Routing abstraction** (`dst.c`) — destination cache entries (`struct dst_entry`).
- **Filter/BPF** (`filter.c`) — classic BPF and eBPF program execution on sockets.
- **Netlink** (`rtnetlink.c`) — route netlink interface for userspace tools
  (ip, iproute2).
- **Network namespace** (`net_namespace.c`) — namespace isolation for networking.
- **Flow dissector** (`flow_dissector.c`) — generic packet header parsing for
  RSS, TC flower, and OVS.
- **Generic Receive Offload** (`gro.c`) — software GRO coalescing.
- **XDP** (`xdp.c`) — eXpress Data Path buffer management.

Source: `net/core/*.c`, `include/net/sock.h`, `include/linux/skbuff.h`,
`include/linux/netdevice.h`.

---

## Subsystem Stack

```
┌─────────────────────────────────────────────────────────────────────┐
│                          USERSPACE                                  │
│                                                                     │
│   socket(2)  send(2)  recv(2)  ioctl(2)   setsockopt(2)           │
│   ip link  ip addr  ip route  ethtool  tc  ss  netstat            │
│   AF_INET  AF_INET6  AF_UNIX  AF_PACKET  AF_NETLINK               │
└───────────────────────────────┬─────────────────────────────────────┘
                                │ syscall boundary
┌───────────────────────────────▼─────────────────────────────────────┐
│                     SOCKET LAYER  (sock.c)                          │
│                                                                     │
│  struct socket ──► struct sock (sk)                                 │
│  sk_alloc / sk_free / sock_setsockopt / sock_getsockopt            │
│  Per-socket memory accounting (sk_wmem / sk_rmem)                  │
│  Socket wait queues, callbacks, locking                            │
└───────────┬───────────────────────────────────┬─────────────────────┘
            │                                   │
    ┌───────▼───────┐                  ┌────────▼────────┐
    │  PROTOCOL     │                  │  NETLINK        │
    │  HANDLERS     │                  │  (af_netlink.c) │
    │               │                  │  rtnetlink.c    │
    │  tcp_sendmsg  │                  │  genetlink      │
    │  udp_sendmsg  │                  └────────┬────────┘
    │  raw_sendmsg  │                           │
    └───────┬───────┘                           │
            │                                   │
┌───────────▼───────────────────────────────────▼─────────────────────┐
│                   SK_BUFF LAYER  (skbuff.c)                         │
│                                                                     │
│  alloc_skb / kfree_skb / skb_clone / skb_copy                     │
│  skb_put / skb_push / skb_pull / skb_reserve                      │
│  skb_linearize / pskb_expand_head                                  │
│                                                                     │
│  ┌──────────────────────────────────────────────┐                  │
│  │  struct sk_buff                               │                  │
│  │  ┌──────┬──────┬──────────────┬──────┐       │                  │
│  │  │ head │ data │   payload    │ tail │ end   │                  │
│  │  └──────┴──────┴──────────────┴──────┘       │                  │
│  │  → transport_header, network_header, mac_hdr │                  │
│  └──────────────────────────────────────────────┘                  │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────────┐
│              DEVICE LAYER  (dev.c / dev_addr_lists.c)               │
│                                                                     │
│  TX path: dev_queue_xmit() → qdisc → ndo_start_xmit()            │
│  RX path: netif_receive_skb() → deliver_skb() → protocol handler  │
│                                                                     │
│  register_netdev / unregister_netdev / netdev_notifier             │
│  dev_set_mtu / dev_change_flags / dev_set_promiscuity             │
│  NAPI: napi_schedule → napi_poll → napi_complete                   │
│                                                                     │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────────┐          │
│  │   Qdisc     │  │     GRO      │  │    XDP           │          │
│  │  (sched/)   │  │  (gro.c)     │  │  (xdp.c)         │          │
│  │  pfifo_fast │  │  gro_receive │  │  xdp_do_redirect  │          │
│  │  fq_codel   │  │  gro_flush   │  │  xdp_buff mgmt   │          │
│  └─────────────┘  └──────────────┘  └──────────────────┘          │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────────┐
│              NEIGHBOUR CACHE  (neighbour.c)                         │
│                                                                     │
│  Generic neighbour resolution framework                            │
│  → ARP for IPv4 (net/ipv4/arp.c)                                  │
│  → NDP for IPv6 (net/ipv6/ndisc.c)                                │
│  neigh_create / neigh_lookup / neigh_resolve_output                │
│  State machine: NUD_INCOMPLETE → NUD_REACHABLE → NUD_STALE        │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────────┐
│              DESTINATION CACHE  (dst.c)                             │
│                                                                     │
│  struct dst_entry — routing decision cache                         │
│  dst_alloc / dst_release / dst_hold                                │
│  Connects L3 routing to L2 neighbour resolution                    │
│  GC via dst_gc_timer                                               │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────────┐
│              NIC DRIVER  (e.g. drivers/net/ethernet/intel/e1000e)   │
│                                                                     │
│  struct net_device_ops → .ndo_start_xmit()                         │
│  DMA ring buffers → hardware TX/RX                                 │
│  IRQ → NAPI poll → netif_receive_skb()                             │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Packet Receive Workflow (RX Path)

```
     NIC hardware interrupt
            │
            ▼
   ┌────────────────────┐
   │  IRQ handler       │
   │  napi_schedule()   │───► schedules NAPI softirq
   └────────────────────┘
            │
            ▼ (softirq NET_RX_SOFTIRQ)
   ┌────────────────────┐
   │  napi_poll()       │
   │  driver->poll()    │───► reads packets from DMA ring
   └────────┬───────────┘
            │ builds sk_buff per packet
            ▼
   ┌────────────────────┐
   │  XDP hook          │───► XDP_DROP / XDP_TX / XDP_REDIRECT
   │  (if prog attached)│     (before sk_buff allocation)
   └────────┬───────────┘
            │ XDP_PASS
            ▼
   ┌────────────────────┐
   │  GRO               │───► coalesce segments
   │  gro_receive()     │     (TCP, UDP with GRO)
   └────────┬───────────┘
            │
            ▼
   ┌────────────────────┐
   │ netif_receive_skb()│
   │  → __netif_receive │
   │     _skb_core()    │───► packet type demux
   └────────┬───────────┘
            │
    ┌───────┴──────────┐
    │                  │
    ▼                  ▼
 ┌──────────┐   ┌──────────────┐
 │ AF_PACKET│   │  ip_rcv()    │
 │ tcpdump  │   │  ipv6_rcv()  │
 └──────────┘   └──────┬───────┘
                       │
                       ▼
                Protocol handler
                (TCP / UDP / ICMP)
                       │
                       ▼
                Socket receive queue
                → wake up userspace
```

---

## Packet Transmit Workflow (TX Path)

```
   Userspace: send(2) / sendmsg(2)
            │
            ▼
   ┌────────────────────┐
   │  sock_sendmsg()    │
   │  → proto->sendmsg()│  (e.g. tcp_sendmsg, udp_sendmsg)
   └────────┬───────────┘
            │ builds sk_buff
            ▼
   ┌────────────────────┐
   │  ip_queue_xmit()   │  (or ip6_xmit)
   │  → route lookup    │
   │  → IP header       │
   └────────┬───────────┘
            │
            ▼
   ┌────────────────────┐
   │  Netfilter hooks   │
   │  NF_INET_LOCAL_OUT │
   │  NF_INET_POST_ROUTE│
   └────────┬───────────┘
            │
            ▼
   ┌────────────────────┐
   │  Neighbour resolve │
   │  neigh_output()    │
   │  → ARP / NDP       │
   └────────┬───────────┘
            │
            ▼
   ┌────────────────────┐
   │  dev_queue_xmit()  │
   │  → TC egress hook  │
   │  → Qdisc enqueue   │
   └────────┬───────────┘
            │
            ▼
   ┌────────────────────┐
   │ ndo_start_xmit()   │
   │  → DMA to NIC      │
   │  → TX completion    │
   └────────────────────┘
```

---

## Key Data Structures

| Structure | File | Purpose |
|---|---|---|
| `struct sk_buff` | `include/linux/skbuff.h` | Universal packet buffer |
| `struct sock` | `include/net/sock.h` | Per-socket kernel state |
| `struct net_device` | `include/linux/netdevice.h` | Network interface |
| `struct dst_entry` | `include/net/dst.h` | Routing cache entry |
| `struct neighbour` | `include/net/neighbour.h` | L2 address resolution |
| `struct napi_struct` | `include/linux/netdevice.h` | NAPI polling context |
| `struct net` | `include/net/net_namespace.h` | Network namespace |

---

## Key Functions

| Function | File | Role |
|---|---|---|
| `netif_receive_skb()` | `dev.c` | Main RX entry point |
| `dev_queue_xmit()` | `dev.c` | Main TX entry point |
| `alloc_skb()` | `skbuff.c` | Allocate sk_buff |
| `kfree_skb()` | `skbuff.c` | Free sk_buff (with drop reason) |
| `sk_alloc()` | `sock.c` | Allocate socket |
| `napi_schedule()` | `dev.c` | Schedule NAPI poll |
| `register_netdev()` | `dev.c` | Register network device |
| `dst_alloc()` | `dst.c` | Allocate dst entry |
| `neigh_create()` | `neighbour.c` | Create neighbour entry |

---

## Analogy

Think of net/core as a **postal sorting facility**:
- **sk_buff** = the package (envelope with headers and payload)
- **net_device** = the delivery truck (physical NIC)
- **sock** = the sender/recipient's mailbox
- **dst_entry** = the routing slip (which truck, which road)
- **neighbour** = the address book (MAC ↔ IP mapping)
- **NAPI** = batch pickup — the truck driver collects many packages at once
  instead of making one trip per package (interrupt coalescing)
- **GRO** = combining small packages going to the same destination into
  one large bundle before sorting
