# Linux Kernel IPv4 Subsystem (`net/ipv4/`)

## Overview

The `net/ipv4/` subsystem implements the Internet Protocol version 4 stack in the
Linux kernel. It is the backbone of most network communication, handling everything
from raw IP packet processing to higher-level protocols like TCP and UDP. The
subsystem covers:

- **IP layer** — `ip_rcv` (receive), `ip_output` (transmit), `ip_forward` (routing
  between interfaces), fragment reassembly, and IP options processing.
- **TCP** — Full reliable-stream transport: connection setup (3-way handshake),
  congestion control (Cubic, BBR, Reno), retransmission, selective acknowledgements
  (SACK), window scaling, and TIME_WAIT handling.
- **UDP** — Connectionless datagram delivery with optional checksums and GRO/GSO
  offload support.
- **ARP** — Address Resolution Protocol for mapping IPv4 addresses to link-layer
  (MAC) addresses on local networks.
- **ICMP** — Internet Control Message Protocol for diagnostics (ping, traceroute)
  and error reporting (destination unreachable, TTL exceeded).
- **Routing / FIB** — Forwarding Information Base that determines next-hop for
  every outgoing packet. Supports policy routing, multiple tables, and ECMP.
- **Connection Tracking (conntrack / netfilter)** — Stateful packet inspection used
  by iptables/nftables for NAT, firewalling, and flow classification.

Source lives in `net/ipv4/` with headers in `include/net/` and `include/linux/`.

---

## IPv4 Stack Architecture

```
 ┌─────────────────────────────────────────────────────────────────────┐
 │                        User Space                                  │
 │   send() / recv() / connect() / bind() / accept()                  │
 └──────────────────────────────┬──────────────────────────────────────┘
                                │  syscall boundary
 ┌──────────────────────────────▼──────────────────────────────────────┐
 │                      Socket Layer (AF_INET)                        │
 │               inet_stream_ops / inet_dgram_ops                     │
 └─────────┬───────────────────────────────────┬───────────────────────┘
           │                                   │
 ┌─────────▼─────────┐             ┌───────────▼───────────┐
 │   TCP  (tcp.c)    │             │   UDP  (udp.c)        │
 │  tcp_sendmsg()    │             │  udp_sendmsg()        │
 │  tcp_v4_rcv()     │             │  udp_rcv()            │
 │  tcp_connect()    │             │  udp_queue_rcv_skb()  │
 │  tcp_retransmit() │             │                       │
 └─────────┬─────────┘             └───────────┬───────────┘
           │                                   │
 ┌─────────▼───────────────────────────────────▼───────────────────────┐
 │                       IP Layer  (ip_output.c / ip_input.c)         │
 │                                                                     │
 │  TX path:  ip_queue_xmit() ──► ip_output() ──► ip_finish_output() │
 │  RX path:  ip_rcv() ──► ip_rcv_finish() ──► ip_local_deliver()    │
 │  Forward:  ip_forward() ──► ip_forward_finish()                    │
 │                                                                     │
 │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐              │
 │  │  Routing/FIB │  │   Netfilter  │  │    ICMP      │              │
 │  │ fib_lookup() │  │  NF_HOOK()   │  │  icmp_rcv()  │              │
 │  │ ip_route_*() │  │  conntrack   │  │  icmp_send() │              │
 │  └──────────────┘  └──────────────┘  └──────────────┘              │
 └─────────────────────────────┬───────────────────────────────────────┘
                               │
 ┌─────────────────────────────▼───────────────────────────────────────┐
 │                    Neighbor / ARP Subsystem                         │
 │              arp_rcv()  /  arp_solicit()  /  neigh_resolve()       │
 └─────────────────────────────┬───────────────────────────────────────┘
                               │
 ┌─────────────────────────────▼───────────────────────────────────────┐
 │                     Network Device (dev.c)                          │
 │            dev_queue_xmit()  /  netif_receive_skb()                │
 └─────────────────────────────┬───────────────────────────────────────┘
                               │
 ┌─────────────────────────────▼───────────────────────────────────────┐
 │                  NIC Driver  (e1000, i40e, mlx5 …)                 │
 │               NAPI poll / TX ring / RX ring / DMA                  │
 └─────────────────────────────────────────────────────────────────────┘
```

---

## RX and TX Packet Flow

### Receive (RX) Path

```
  NIC hardware interrupt
        │
        ▼
  napi_schedule()  ──►  napi_poll()  ──►  netif_receive_skb()
                                                │
                                                ▼
                                    ┌── NF_INET_PRE_ROUTING ──┐
                                    │                          │
                                    ▼                          │
                              ip_rcv()                         │
                                    │                          │
                                    ▼                          │
                          ip_rcv_finish()                      │
                                    │                          │
                         ┌──────────┴──────────┐               │
                         │ route lookup        │               │
                         │ ip_route_input_noref│               │
                         └──────────┬──────────┘               │
                                    │                          │
                    ┌───────────────┼───────────────┐          │
                    │ local?        │ forward?      │          │
                    ▼               ▼               │          │
          ip_local_deliver()   ip_forward()         │          │
                    │               │               │          │
                    ▼               ▼               │          │
          NF_INET_LOCAL_IN   NF_INET_FORWARD        │          │
                    │               │               │          │
                    ▼               ▼               │          │
          tcp_v4_rcv()       ip_forward_finish()    │          │
          udp_rcv()               │                 │          │
          icmp_rcv()              ▼                  │          │
                          NF_INET_POST_ROUTING      │          │
                                  │                 │          │
                                  ▼                 │          │
                           dev_queue_xmit()         │          │
                                                    │          │
                                                    ▼          │
                                              (packet out)     │
                                                               │
```

### Transmit (TX) Path

```
  send() / sendmsg()
        │
        ▼
  tcp_sendmsg() / udp_sendmsg()
        │
        ▼
  ip_queue_xmit()  ──or──  ip_push_pending_frames()
        │
        ▼
  ip_output()
        │
        ▼
  NF_INET_LOCAL_OUT
        │
        ▼
  ip_output() ──► dst_output()
        │
        ▼
  NF_INET_POST_ROUTING
        │
        ▼
  ip_finish_output() ──► ip_finish_output2()
        │
        ▼
  neigh_resolve_output()  (ARP resolution if needed)
        │
        ▼
  dev_queue_xmit()  ──►  NIC driver TX ring
```

---

## Key Data Structures

| Structure | Header | Purpose |
|---|---|---|
| `struct sk_buff` | `include/linux/skbuff.h` | Universal packet buffer; carries every packet through the stack |
| `struct iphdr` | `include/uapi/linux/ip.h` | IPv4 header (version, IHL, TTL, protocol, src/dst addr) |
| `struct inet_sock` | `include/net/inet_sock.h` | IPv4-specific socket state; embeds `struct sock`, adds IP options, TTL, TOS |
| `struct tcp_sock` | `include/linux/tcp.h` | TCP-specific state: sequence numbers, windows, congestion, timers |
| `struct udp_sock` | `include/net/udp.h` | UDP-specific state: pending frames, corking, encap hooks |
| `struct rtable` | `include/net/route.h` | IPv4 routing cache entry — result of a route lookup |
| `struct fib_info` | `include/net/ip_fib.h` | FIB route metadata: next-hops, metrics, device references |
| `struct fib_table` | `include/net/ip_fib.h` | A routing table (main, local, or policy tables) |
| `struct flowi4` | `include/net/flow.h` | Flow key for IPv4 route lookups (src, dst, tos, oif, protocol) |
| `struct net_protocol` | `include/net/protocol.h` | Registered L4 protocol handler (tcp, udp, icmp, etc.) |
| `struct dst_entry` | `include/net/dst.h` | Generic destination cache entry; `struct rtable` embeds this |
| `struct neighbour` | `include/net/neighbour.h` | ARP/neighbor cache entry: maps L3 addr → L2 addr |
| `struct nf_hook_state` | `include/linux/netfilter.h` | State passed to netfilter hooks (used by iptables/nftables) |
| `struct inet_connection_sock` | `include/net/inet_connection_sock.h` | Connection-oriented socket base: accept queue, ICMP handling |

---

## Key Functions

| Function | Source File | Role |
|---|---|---|
| `ip_rcv()` | `net/ipv4/ip_input.c` | Main IPv4 RX entry; validates header, invokes PRE_ROUTING hook |
| `ip_rcv_finish()` | `net/ipv4/ip_input.c` | Post-hook RX processing; performs route lookup |
| `ip_local_deliver()` | `net/ipv4/ip_input.c` | Delivers packet to local L4 protocol handler |
| `ip_output()` | `net/ipv4/ip_output.c` | Main IPv4 TX entry; sets IP header fields |
| `ip_finish_output()` | `net/ipv4/ip_output.c` | Handles fragmentation, calls neighbor resolution |
| `ip_queue_xmit()` | `net/ipv4/ip_output.c` | TX entry for TCP; performs route lookup + `ip_output()` |
| `ip_forward()` | `net/ipv4/ip_forward.c` | Forwards packets between interfaces; decrements TTL |
| `ip_route_input_noref()` | `net/ipv4/route.c` | RX-path route lookup; determines local vs forward vs drop |
| `ip_route_output_flow()` | `net/ipv4/route.c` | TX-path route lookup for outgoing packets |
| `fib_lookup()` | `net/ipv4/fib_rules.c` | Core FIB lookup; consults all policy-routing tables |
| `tcp_v4_rcv()` | `net/ipv4/tcp_ipv4.c` | TCP RX entry; demuxes to socket, enters TCP state machine |
| `tcp_sendmsg()` | `net/ipv4/tcp.c` | Copies user data into TCP send buffer |
| `tcp_connect()` | `net/ipv4/tcp_output.c` | Initiates TCP 3-way handshake (sends SYN) |
| `tcp_retransmit_skb()` | `net/ipv4/tcp_output.c` | Retransmits a lost TCP segment |
| `udp_rcv()` | `net/ipv4/udp.c` | UDP RX entry; looks up socket, delivers datagram |
| `udp_sendmsg()` | `net/ipv4/udp.c` | Builds and sends a UDP datagram |
| `arp_rcv()` | `net/ipv4/arp.c` | Processes incoming ARP requests/replies |
| `arp_solicit()` | `net/ipv4/arp.c` | Sends ARP request to resolve neighbor address |
| `icmp_rcv()` | `net/ipv4/icmp.c` | Processes incoming ICMP messages (echo, errors) |
| `icmp_send()` | `net/ipv4/icmp.c` | Sends ICMP error messages |

---

## Analogy: The IPv4 Stack as a Postal System

Think of the IPv4 subsystem as a **national postal service**:

- **Socket layer** = the mailbox slot at your house. You drop letters in (send) or
  pick them up (recv). You don't care about the trucks.
- **TCP** = registered mail with tracking. Every letter gets a sequence number. The
  recipient signs for each one. If a letter is lost, the post office automatically
  re-sends it (`tcp_retransmit_skb`). The sender adjusts how fast it sends based on
  how congested the roads are (congestion control).
- **UDP** = postcards. Cheap, fast, no tracking. If one gets lost, nobody notices.
  Great for things like broadcast announcements.
- **IP layer** = the postal sorting center. It reads the destination address on the
  envelope (`struct iphdr`), looks up which route to take (`fib_lookup`), and
  either delivers locally (`ip_local_deliver`) or forwards to the next sorting
  center (`ip_forward`).
- **Routing / FIB** = the big route-planning map on the wall of the sorting center.
  It tells workers: "For addresses 10.0.0.0/8, send to truck bay 3; for
  192.168.1.0/24, use the local van."
- **ARP** = the last-mile lookup. The van driver knows the street address but needs
  the actual house location (MAC address). ARP is like asking a neighbor: "Hey,
  who lives at 192.168.1.5?"
- **ICMP** = the "return to sender" stamp. If a letter can't be delivered (host
  unreachable, TTL exceeded), ICMP sends back a notice. Ping is like sending a
  "are you still there?" postcard and waiting for the reply.
- **Netfilter / conntrack** = the customs checkpoint. Every package passing through
  gets inspected. The inspector remembers ongoing conversations (stateful tracking)
  and can rewrite sender addresses (NAT) so internal offices can share one public
  mailbox.

---

## Further Reading

- `Documentation/networking/ip-sysctl.rst` — Tunable parameters for the IPv4 stack
- `net/ipv4/Makefile` — Build targets showing all source files in the subsystem
- `include/net/tcp.h` — Core TCP inline helpers and constants
- `include/net/ip.h` — Core IP layer function declarations
- `tools/testing/selftests/net/` — Kernel self-tests for networking
