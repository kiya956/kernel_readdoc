# Linux Kernel IPv6 Subsystem (`net/ipv6/`)

## Overview

The IPv6 subsystem implements the Internet Protocol version 6 stack in the
Linux kernel.  It lives under `net/ipv6/` and provides the full data-plane
and control-plane needed to send, receive, route, and forward 128-bit
addressed packets.

### Major components

| Component | Source files | Purpose |
|-----------|-------------|---------|
| **Core receive** | `ip6_input.c` | `ipv6_rcv()` — entry from the network device layer |
| **Core transmit** | `ip6_output.c` | `ip6_output()`, `ip6_xmit()` — build and send IPv6 packets |
| **TCP over IPv6** | `tcp_ipv6.c` | `tcp_v6_rcv()` — TCPv6 segment processing |
| **UDP over IPv6** | `udp.c` (ipv6) | `udpv6_rcv()` — UDPv6 datagram processing |
| **ICMPv6** | `icmp.c` | `icmpv6_rcv()` — echo, errors, MLD |
| **NDP** | `ndisc.c` | `ndisc_rcv()` — Neighbor Discovery (NS/NA/RS/RA) |
| **SLAAC / DAD** | `addrconf.c` | `addrconf_dad_completed()` — Stateless Address Autoconfiguration |
| **Routing** | `route.c`, `ip6_fib.c` | `ip6_route_input_lookup()`, `fib6_lookup()` — FIB6 / routing table |
| **Forwarding** | `ip6_output.c` | `ip6_forward()` — packet forwarding between interfaces |
| **Exthdrs** | `exthdrs.c` | Extension header parsing (hop-by-hop, routing, fragment, dest) |
| **Fragmentation** | `reassembly.c` | IPv6 fragment reassembly |
| **Multicast** | `mcast.c` | MLD v1/v2, multicast group management |
| **Raw sockets** | `raw.c` | `rawv6_rcv()` — raw IPv6 socket delivery |
| **Netfilter** | `netfilter/` | `ip6_tables`, `nf_conntrack` IPv6 hooks |

---

## Full IPv6 Stack — ASCII Diagram

```
 ┌─────────────────────────────────────────────────────────────────────┐
 │                       User-space applications                      │
 │                  (socket API: AF_INET6, SOCK_STREAM/DGRAM)         │
 └───────────────────────────┬────────────────────────────────────────-┘
                             │  send()/recv()
 ┌───────────────────────────▼─────────────────────────────────────────┐
 │                      Socket Layer (BSD sockets)                     │
 │              inet6_stream_ops / inet6_dgram_ops                     │
 └──────────┬──────────────────────────────────┬───────────────────────┘
            │                                  │
 ┌──────────▼──────────┐            ┌──────────▼──────────┐
 │   TCPv6 (tcp_ipv6)  │            │   UDPv6 (udp_v6)    │
 │   tcp_v6_rcv()      │            │   udpv6_rcv()        │
 │   tcp_v6_connect()  │            │   udpv6_sendmsg()    │
 └──────────┬──────────┘            └──────────┬───────────┘
            │                                  │
 ┌──────────▼──────────────────────────────────▼───────────────────────┐
 │                 IPv6 Core (ip6_input / ip6_output)                   │
 │                                                                     │
 │  RX: ipv6_rcv() ──► ip6_input() ──► transport deliver               │
 │  TX: ip6_output() ──► ip6_finish_output() ──► dev_queue_xmit()      │
 │  FWD: ip6_forward() ──► ip6_output()                                │
 │                                                                     │
 │  ┌───────────────┐  ┌────────────┐  ┌──────────────────┐            │
 │  │ Extension Hdr │  │ Fragment / │  │ Netfilter Hooks  │            │
 │  │ exthdrs.c     │  │ Reassembly │  │ NF_INET_PRE/POST │            │
 │  └───────────────┘  └────────────┘  └──────────────────┘            │
 └──────────┬──────────────────────────────────┬───────────────────────┘
            │                                  │
 ┌──────────▼──────────┐            ┌──────────▼──────────┐
 │   ICMPv6 (icmp.c)   │            │   NDP (ndisc.c)     │
 │   icmpv6_rcv()      │            │   ndisc_rcv()       │
 │   icmpv6_send()     │            │   NS / NA / RS / RA │
 └─────────────────────┘            └──────────┬──────────┘
                                               │
                              ┌────────────────▼──────────────────┐
                              │  SLAAC / DAD  (addrconf.c)        │
                              │  addrconf_dad_completed()         │
                              │  addrconf_notify()                │
                              └───────────────────────────────────┘
            │
 ┌──────────▼──────────────────────────────────────────────────────────┐
 │                    Routing / FIB6 Subsystem                         │
 │                                                                     │
 │  ip6_route_input_lookup()  ──►  fib6_lookup()                       │
 │  fib6_table / fib6_node / fib6_info                                 │
 │  Neighbor cache (struct neighbour ──► ndisc_solicit)                 │
 └──────────┬──────────────────────────────────────────────────────────┘
            │
 ┌──────────▼──────────────────────────────────────────────────────────┐
 │               Network Device Layer (dev.c / L2)                     │
 │        dev_queue_xmit()  /  netif_receive_skb()                     │
 │        ┌──────────┐  ┌──────────┐  ┌──────────┐                    │
 │        │   eth0   │  │  wlan0   │  │   lo     │                    │
 │        └──────────┘  └──────────┘  └──────────┘                    │
 └─────────────────────────────────────────────────────────────────────┘
```

---

## IPv6 RX / TX Packet Flow (including NDP & SLAAC)

```
                            ╔═══════════════╗
                            ║   NIC / Wire  ║
                            ╚══════╤════════╝
  ═══════════════════ RX PATH ═════╪══════════════════════════════════
                                   │
                         netif_receive_skb()
                                   │
                         ┌─────────▼─────────┐
                         │    ipv6_rcv()      │  net/ipv6/ip6_input.c
                         │  (NF_INET_PRE)     │
                         └─────────┬─────────┘
                                   │
                    ┌──────────────┼───────────────┐
                    │              │               │
              is_local?      is_forward?      is_multicast?
                    │              │               │
            ┌───────▼───────┐  ┌──▼──────┐  ┌─────▼──────┐
            │ ip6_input()   │  │ip6_fwd()│  │ ip6_mc_    │
            │               │  │         │  │ input()    │
            └───────┬───────┘  └──┬──────┘  └─────┬──────┘
                    │             │               │
         ┌──────┬──┴──┬─────┐    │               │
         │      │     │     │    │               │
       TCP    UDP  ICMPv6  NDP   │          MLD/multicast
         │      │     │     │    │
  tcp_v6_rcv  udpv6 icmpv6 ndisc_rcv
              _rcv  _rcv    │
                            │
                   ┌────────▼────────┐
                   │ Process NDP:    │
                   │  NS → reply NA  │
                   │  NA → update    │
                   │       neigh     │
                   │  RS → (router)  │
                   │  RA → SLAAC     │
                   └────────┬────────┘
                            │  (RA received)
                   ┌────────▼────────┐
                   │ addrconf_prefix │
                   │  _rcv_add_addr()│
                   │  Start DAD      │
                   └────────┬────────┘
                            │  (DAD ok)
                   ┌────────▼────────────┐
                   │ addrconf_dad_       │
                   │   completed()       │
                   │ Address → PERMANENT │
                   └─────────────────────┘

  ═══════════════════ TX PATH ═══════════════════════════════════════
                                   │
                    socket sendmsg() / kernel ip6_xmit()
                                   │
                         ┌─────────▼─────────┐
                         │  ip6_route_output  │  route lookup
                         │  fib6_lookup()     │
                         └─────────┬─────────┘
                                   │
                         ┌─────────▼─────────┐
                         │  ip6_output()      │  net/ipv6/ip6_output.c
                         │  (NF_INET_POST)    │
                         └─────────┬─────────┘
                                   │
                    ┌──────────────┼──────────────┐
                    │              │              │
              need frag?    need neigh?     ready to go
                    │              │              │
             ip6_fragment   ndisc_solicit   ip6_finish_output()
                    │              │              │
                    └──────────────┼──────────────┘
                                   │
                         dev_queue_xmit()
                                   │
                            ╔══════╧════════╗
                            ║   NIC / Wire  ║
                            ╚═══════════════╝
```

---

## Key Data Structures

| Structure | Header | Purpose |
|-----------|--------|---------|
| `struct ipv6hdr` | `<linux/ipv6.h>` | 40-byte fixed IPv6 header (version, flow, hop_limit, src/dst addr) |
| `struct inet6_skb_parm` | `<net/ipv6.h>` | Per-skb IPv6 control block (cb[]) — hop-by-hop offset, frag info |
| `struct inet6_dev` | `<net/if_inet6.h>` | Per-netdev IPv6 state: address list, DAD, RA, multicast, sysctl |
| `struct inet6_ifaddr` | `<net/if_inet6.h>` | Single IPv6 address on a device (prefix len, scope, DAD state, timers) |
| `struct ipv6_pinfo` | `<linux/ipv6.h>` | Per-socket IPv6 options (flow label, hop limit, ext headers) |
| `struct rt6_info` | `<net/ip6_route.h>` | IPv6 routing cache entry wrapping `struct dst_entry` |
| `struct fib6_info` | `<net/ip6_fib.h>` | FIB6 route entry (prefix, nexthop, metric, expiry, flags) |
| `struct fib6_node` | `<net/ip6_fib.h>` | Radix-tree node in the FIB6 lookup trie |
| `struct fib6_table` | `<net/ip6_fib.h>` | FIB6 routing table (RT6_TABLE_MAIN = 254) |
| `struct neighbour` | `<net/neighbour.h>` | Neighbor cache entry (L3→L2 mapping, NUD state machine) |
| `struct ndisc_options` | `<net/ndisc.h>` | Parsed NDP option fields (source/target LL addr, prefix, MTU) |
| `struct flowi6` | `<net/flow.h>` | Flow key for IPv6 route lookup (saddr, daddr, ports, oif, mark) |
| `struct icmp6hdr` | `<linux/icmpv6.h>` | ICMPv6 header (type, code, checksum + body union) |
| `struct in6_addr` | `<linux/in6.h>` | 128-bit IPv6 address |

---

## Key Functions

| Function | File | Purpose |
|----------|------|---------|
| `ipv6_rcv()` | `ip6_input.c` | Main IPv6 RX entry — called by `netif_receive_skb()` via `ipv6_packet_type` |
| `ip6_input()` | `ip6_input.c` | Deliver locally-destined packets to transport protocols |
| `ip6_output()` | `ip6_output.c` | Main IPv6 TX — netfilter POST_ROUTING, then `ip6_finish_output()` |
| `ip6_xmit()` | `ip6_output.c` | Build IPv6 header and hand to `ip6_output()` |
| `ip6_forward()` | `ip6_output.c` | Forward transit packets — decrement hop limit, route, output |
| `ip6_fragment()` | `ip6_output.c` | Fragment oversized IPv6 datagrams |
| `tcp_v6_rcv()` | `tcp_ipv6.c` | TCPv6 segment receive — lookup socket, deliver |
| `udpv6_rcv()` | `udp.c` | UDPv6 datagram receive — lookup socket, deliver |
| `icmpv6_rcv()` | `icmp.c` | ICMPv6 receive — echo reply, error handling, redirect |
| `icmpv6_send()` | `icmp.c` | Send ICMPv6 error messages (dest unreach, pkt too big) |
| `ndisc_rcv()` | `ndisc.c` | NDP message receive — dispatch NS/NA/RS/RA/Redirect |
| `ndisc_solicit()` | `ndisc.c` | Send Neighbor Solicitation |
| `addrconf_dad_completed()` | `addrconf.c` | DAD finished — transition address to PERMANENT |
| `addrconf_prefix_rcv_add_addr()` | `addrconf.c` | SLAAC — add auto-configured address from RA prefix |
| `ip6_route_input_lookup()` | `route.c` | RX-path route lookup (FIB6 + policy routing) |
| `ip6_route_output_flags()` | `route.c` | TX-path route lookup |
| `fib6_lookup()` | `ip6_fib.c` | Core FIB6 trie lookup — longest prefix match |
| `fib6_add()` / `fib6_del()` | `ip6_fib.c` | Insert / delete routes in FIB6 |
| `inet6_add_protocol()` | `protocol.c` | Register upper-layer protocol (TCP, UDP, ICMPv6) |

---

## Practical Analogy

Think of the IPv6 subsystem as a **city postal system redesigned for a
much larger world**:

- **IPv6 addresses** are like 128-bit postal codes — so large that every
  grain of sand on Earth could have its own address.
- **`ipv6_rcv()`** is the central post office sorting room: every
  incoming letter (packet) arrives here first.
- **Routing / FIB6** is the big map on the wall — the postmaster looks
  up the longest matching prefix to decide which delivery van to use.
- **`ip6_forward()`** is the relay truck that carries mail between cities
  (interfaces) without opening it.
- **ICMPv6** is the "return-to-sender" service — if a letter is
  undeliverable, an error notice goes back.
- **NDP (Neighbor Discovery)** replaces the old ARP phone book.  Instead
  of shouting "Who has this address?" on the whole street (broadcast),
  NDP sends a targeted multicast postcard (Neighbor Solicitation) and
  the recipient replies with a Neighbor Advertisement.
- **SLAAC** is like a new resident moving in and auto-generating their
  own house number from the street prefix (Router Advertisement) plus
  their unique ID (EUI-64 / privacy extensions).  DAD is knocking on
  doors to make sure nobody already has that house number.
- **Extension headers** are extra labels stapled to the envelope
  (hop-by-hop options, routing waypoints, fragmentation stamps).

The entire system is designed so that *no broadcasts exist* — everything
uses multicast or unicast, making the postal system quieter and more
efficient than the old IPv4 town.

---

## Further Reading

- `Documentation/networking/ipv6.rst` in the kernel tree
- RFC 8200 — Internet Protocol, Version 6 (IPv6) Specification
- RFC 4861 — Neighbor Discovery for IP version 6
- RFC 4862 — IPv6 Stateless Address Autoconfiguration
- `net/ipv6/Kconfig` — kernel build options for IPv6
