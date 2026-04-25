# SCTP — Stream Control Transmission Protocol

## Overview

**SCTP (Stream Control Transmission Protocol)**, defined in RFC 9260, is a
reliable, message-oriented transport protocol supporting:

- **Multi-homing** — an association can span multiple IP addresses for failover
- **Multi-streaming** — multiple independent streams within one association
  (avoids head-of-line blocking)
- **4-way handshake** — INIT → INIT-ACK → COOKIE-ECHO → COOKIE-ACK
  (protects against SYN-flood attacks with a cookie mechanism)
- **Chunk-based framing** — data, control, and error messages are "chunks"
  multiplexed in SCTP packets

SCTP is used in telecom signaling (SIGTRAN, Diameter, S1AP/X2AP in LTE),
WebRTC data channels, and high-availability applications.

Source: `net/sctp/`, `include/net/sctp/`.

---

## Subsystem Stack

```
┌──────────────────────────────────────────────────────────────────┐
│                        USER SPACE                                │
│  socket(AF_INET, SOCK_STREAM, IPPROTO_SCTP)                     │
│  socket(AF_INET, SOCK_SEQPACKET, IPPROTO_SCTP)                  │
│  sctp_sendmsg() / sctp_recvmsg() via sendmsg/recvmsg            │
└──────────────────────────────┬───────────────────────────────────┘
                               │
┌──────────────────────────────▼───────────────────────────────────┐
│                SCTP CORE  (net/sctp/)                             │
│                                                                   │
│  sctp_rcv()          — inbound packet entry, dispatch to assoc   │
│  sctp_sendmsg()      — outbound: chunk creation, bundling        │
│  sctp_do_sm()        — state machine: process events/commands    │
│                                                                   │
│  ┌────────────────────────────────────────────────────────┐      │
│  │  struct sctp_association                                │      │
│  │   ├─ peer transport addresses (multi-homing)           │      │
│  │   ├─ streams[] (multi-streaming)                       │      │
│  │   ├─ outqueue → retransmit queue                       │      │
│  │   └─ state (CLOSED→COOKIE_WAIT→ESTABLISHED→SHUTDOWN)  │      │
│  └────────────────────────────────────────────────────────┘      │
│                                                                   │
│  struct sctp_endpoint   — local endpoint: port, socket binding   │
│  struct sctp_chunk      — individual chunk: DATA, SACK, INIT…    │
└──────────────────────────────┬───────────────────────────────────┘
                               │
┌──────────────────────────────▼───────────────────────────────────┐
│                      IP LAYER (IPv4/IPv6)                        │
│  SCTP registers as IPPROTO_SCTP (132) in inet_protos             │
└──────────────────────────────────────────────────────────────────┘
```

---

## 4-Way Handshake

```
  Initiator                          Responder
     │                                   │
     │──── INIT ────────────────────────>│
     │     (my tag, my addresses,        │
     │      requested streams)           │
     │                                   │
     │<──── INIT-ACK ──────────────────│
     │      (responder tag, cookie)      │
     │                                   │
     │──── COOKIE-ECHO ────────────────>│
     │     (echo back the cookie)        │
     │                                   │
     │<──── COOKIE-ACK ────────────────│
     │      ESTABLISHED                  │
     │                                   │
```

The cookie mechanism means the responder keeps **no state** until the
COOKIE-ECHO proves the initiator is reachable — a built-in SYN-flood defense.

---

## Key Data Structures

| Structure | Purpose |
|---|---|
| `struct sctp_association` | Full association state: peer addrs, streams, TSN tracking |
| `struct sctp_endpoint` | Local endpoint: bound port, associations list |
| `struct sctp_chunk` | Single SCTP chunk: type, flags, skb, transport |
| `struct sctp_transport` | Per-peer-address state: RTT, cwnd, RTO |
| `struct sctp_outq` | Output queue: pending DATA, retransmit list |
| `struct sctp_stream` | Stream state: SSN tracking per stream |

---

## Key Functions

| Function | Role |
|---|---|
| `sctp_rcv()` | Main receive entry: lookup association, dispatch chunk |
| `sctp_sendmsg()` | Send path: create DATA chunks, enqueue to outq |
| `sctp_do_sm()` | State machine: event × state → action + new state |
| `sctp_association_new()` | Allocate and initialize a new association |
| `sctp_endpoint_new()` | Create endpoint for a socket |
| `sctp_connect()` | Initiate association: send INIT chunk |
| `sctp_bind()` | Bind endpoint to local address/port |
| `sctp_init_sock()` | Initialize SCTP socket options and defaults |
| `sctp_chunk_new()` | Allocate a new chunk structure |

---

## Key Source Files

| File | Purpose |
|---|---|
| `net/sctp/input.c` | sctp_rcv — packet receive and association lookup |
| `net/sctp/output.c` | Chunk bundling and packet construction |
| `net/sctp/sm_statefuns.c` | State machine action functions |
| `net/sctp/sm_sideeffect.c` | State machine side effects (timers, send) |
| `net/sctp/socket.c` | Socket operations: bind, connect, sendmsg |
| `net/sctp/associola.c` | Association management |
| `net/sctp/endpointola.c` | Endpoint management |
| `include/net/sctp/structs.h` | Core data structures |

---

## Analogy

SCTP is like a **multi-lane toll highway with backup routes**:

- **Multi-streaming** is having multiple lanes: a slow truck in lane 1
  doesn't block cars in lane 2 (no head-of-line blocking).
- **Multi-homing** is having alternate highway entrances: if one entrance
  is blocked (interface down), traffic automatically reroutes through another.
- The **4-way handshake** is like requiring a signed reservation (cookie)
  before the toll booth allocates a lane — no one can flood the booth with
  fake reservations.
- Each **chunk** is a self-contained envelope in the truck: DATA chunks
  carry payload, SACK chunks are delivery receipts, HEARTBEAT chunks are
  "are you still there?" pings.

---

## References

- `net/sctp/` — kernel implementation
- `include/net/sctp/structs.h` — core structures
- RFC 9260 — SCTP specification (replaces RFC 4960)
- RFC 6458 — SCTP sockets API
- `Documentation/networking/sctp.rst`
