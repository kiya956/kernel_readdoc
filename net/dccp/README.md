# DCCP — Datagram Congestion Control Protocol

## Overview

**DCCP (Datagram Congestion Control Protocol)**, defined in RFC 4340, is an
**unreliable** transport protocol with **built-in congestion control**.  It
fills the gap between TCP (reliable, congestion-controlled) and UDP (unreliable,
no congestion control), making it suitable for real-time applications like
streaming media and VoIP that need congestion awareness without reliability.

Key features:
- **Unreliable delivery** — no retransmission, like UDP
- **Congestion control** — pluggable CCIDs (Congestion Control IDs):
  CCID 2 (TCP-like AIMD), CCID 3 (TCP-Friendly Rate Control)
- **Feature negotiation** — endpoints negotiate CCIDs, ECN, etc. during setup
- **3-way handshake** — Request → Response → Ack (with optional Listen)

Source: `net/dccp/`, `include/linux/dccp.h`.

---

## Subsystem Stack

```
┌──────────────────────────────────────────────────────────────────┐
│                        USER SPACE                                │
│  socket(AF_INET, SOCK_DCCP, IPPROTO_DCCP)                       │
│  connect() / listen() / accept() / sendmsg() / recvmsg()        │
└──────────────────────────────┬───────────────────────────────────┘
                               │
┌──────────────────────────────▼───────────────────────────────────┐
│                DCCP CORE  (net/dccp/)                             │
│                                                                   │
│  dccp_rcv()           — inbound packet processing                │
│  dccp_sendmsg()       — outbound datagram send                   │
│  dccp_connect()       — initiate 3-way handshake (Request)       │
│  dccp_v4_rcv()        — IPv4 receive entry point                 │
│  dccp_close()         — connection teardown (Close/Reset)        │
│                                                                   │
│  ┌────────────────────────────────────────────────────────┐      │
│  │  Connection States                                      │      │
│  │  CLOSED → REQUEST → RESPOND → PARTOPEN → OPEN          │      │
│  │  OPEN → CLOSING → TIMEWAIT → CLOSED                    │      │
│  └────────────────────────────────────────────────────────┘      │
│                                                                   │
│  ┌──────────────┐  ┌──────────────┐                              │
│  │ CCID 2       │  │ CCID 3       │   Pluggable congestion      │
│  │ (TCP-like)   │  │ (TFRC)       │   control modules           │
│  └──────────────┘  └──────────────┘                              │
└──────────────────────────────┬───────────────────────────────────┘
                               │
┌──────────────────────────────▼───────────────────────────────────┐
│                      IP LAYER (IPv4/IPv6)                        │
│  DCCP registers as IPPROTO_DCCP (33) in inet_protos              │
└──────────────────────────────────────────────────────────────────┘
```

---

## 3-Way Handshake

```
  Client                             Server
     │                                  │
     │──── DCCP-Request ──────────────>│
     │     (service code, features)     │
     │                                  │
     │<──── DCCP-Response ─────────────│
     │      (ack, features)             │
     │                                  │
     │──── DCCP-Ack ──────────────────>│
     │      PARTOPEN → OPEN             │
     │                                  │
     │←──→ DCCP-Data / DCCP-DataAck ←──→│
     │                                  │
     │──── DCCP-Close ────────────────>│
     │<──── DCCP-Reset ────────────────│
     │      TIMEWAIT → CLOSED           │
```

---

## Key Data Structures

| Structure | Purpose |
|---|---|
| `struct dccp_sock` | Per-connection DCCP socket state (extends inet_sock) |
| `struct dccp_skb_cb` | Per-skb control block: sequence numbers, type |
| `struct ccid` | Congestion control module interface |
| `struct dccp_request_sock` | Connection request state (3-way handshake) |
| `struct dccp_ackvec` | Ack vector: bitmap of received/lost packets |
| `struct dccp_service_list` | Service codes offered by a listening socket |

---

## Key Functions

| Function | Role |
|---|---|
| `dccp_rcv()` | Receive path: state machine processing for incoming packets |
| `dccp_sendmsg()` | Send a datagram: create DCCP-Data packet |
| `dccp_connect()` | Initiate connection: send DCCP-Request |
| `dccp_v4_rcv()` | IPv4 receive entry: lookup socket, call dccp_rcv |
| `dccp_create_openreq_child()` | Create child socket from connection request |
| `dccp_close()` | Connection teardown: send Close, enter TIMEWAIT |
| `dccp_init_sock()` | Initialize socket defaults and CCID |
| `dccp_setsockopt()` | Socket option handling (CCID, service code, etc.) |
| `inet_dccp_listen()` | Transition socket to LISTEN state |

---

## Key Source Files

| File | Purpose |
|---|---|
| `net/dccp/proto.c` | Socket operations: connect, sendmsg, close |
| `net/dccp/input.c` | Receive processing and state machine |
| `net/dccp/output.c` | Packet construction and transmission |
| `net/dccp/ipv4.c` | IPv4-specific receive/transmit |
| `net/dccp/ipv6.c` | IPv6-specific receive/transmit |
| `net/dccp/options.c` | Feature negotiation and option parsing |
| `net/dccp/ccids/ccid2.c` | CCID 2: TCP-like congestion control |
| `net/dccp/ccids/ccid3.c` | CCID 3: TCP-Friendly Rate Control |
| `include/linux/dccp.h` | Core structures and constants |

---

## Analogy

DCCP is like a **courier service that doesn't guarantee delivery but
avoids traffic jams**:

- Unlike TCP (a tracked, guaranteed-delivery postal service), DCCP sends
  packages without tracking — if one is lost, it's gone (unreliable).
- Unlike UDP (tossing packages out the window hoping they arrive), DCCP
  watches the road conditions (congestion) and slows down when there's
  traffic, being a good citizen of the network.
- The **CCID** is the driving strategy: CCID 2 drives like a TCP driver
  (speed up until packet loss, then slow down), CCID 3 drives at a steady
  "TCP-friendly" rate to be fair without the saw-tooth pattern.
- Perfect for live video/audio: you'd rather drop a frame than wait for
  a retransmission that arrives too late to be useful.

---

## References

- `net/dccp/` — kernel implementation
- `include/linux/dccp.h` — structures
- RFC 4340 — DCCP specification
- RFC 4341 — CCID 2 (TCP-like)
- RFC 4342 — CCID 3 (TFRC)
