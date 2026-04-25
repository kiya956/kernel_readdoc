# X.25 Packet-Layer Protocol Subsystem (`net/x25`)

## Overview

X.25 is an ITU-T standard for packet-switched WAN communication over virtual
circuits. Developed in the 1970s, it was the backbone of early data networks
(like Tymnet and DATAPAC) before IP took over. The Linux kernel implements the
Packet Layer Protocol (PLP — layer 3 of X.25), providing connection-oriented
virtual circuit service over LAPB (Link Access Procedure Balanced — layer 2).

X.25 uses **virtual circuits** (VCs) — logical connections identified by
Logical Channel Numbers (LCN). Circuits can be Switched Virtual Circuits
(SVC, on-demand) or Permanent Virtual Circuits (PVC, pre-provisioned).

## Architecture

```
┌─────────────────────────────────────────────────────┐
│           Application (AF_X25 socket)                │
│        socket(AF_X25, SOCK_SEQPACKET, 0)             │
├─────────────────────────────────────────────────────┤
│              X.25 Packet Layer (PLP)                  │
│  ┌────────────────────────────────────────────────┐  │
│  │            struct x25_sock                      │  │
│  │   (LCN, state, facilities, X.121 address)       │  │
│  │                                                 │  │
│  │  ┌──────────────┐  ┌────────────────────────┐   │  │
│  │  │ Routing Table│  │ Virtual Circuit State  │   │  │
│  │  │ (x25_route)  │  │ Machine (per socket)   │   │  │
│  │  └──────────────┘  └────────────────────────┘   │  │
│  └────────────────────────────────────────────────┘  │
├─────────────────────────────────────────────────────┤
│              LAPB (Layer 2)                           │
│         (frame-level error recovery)                  │
├─────────────────────────────────────────────────────┤
│           Physical Interface (serial/WAN)             │
└─────────────────────────────────────────────────────┘
```

## Virtual Circuit Lifecycle

```
  Caller (DTE)                           Callee (DTE)
      │                                      │
      │  connect(X.121 address)               │
      │                                      │
      │──── CALL REQUEST (LCN=N) ────────────►│
      │     (src addr, dst addr, facilities)   │
      │                                      │
      │◄──── CALL ACCEPTED (LCN=N) ──────────│
      │                                      │
      │     [Virtual Circuit established]      │
      │                                      │
      │════ DATA (P(S), P(R)) ═══════════════►│
      │◄════ DATA (P(S), P(R)) ═══════════════│
      │     (flow-controlled, sequenced)       │
      │                                      │
      │──── CLEAR REQUEST ───────────────────►│
      │◄──── CLEAR CONFIRMATION ──────────────│
      │                                      │

  VC State Machine:
    READY ──► CALL_SENT ──► DATA_TRANSFER ──► CLEAR_REQ ──► READY
    READY ──► CALL_INDICATED ──► DATA_TRANSFER ──► ...
```

## Key Structures

| Structure          | Description                                                |
|--------------------|------------------------------------------------------------|
| `struct x25_sock`  | X.25 socket — LCN, state, X.121 address, facilities       |
| `struct x25_route` | Routing entry — X.121 prefix → network device mapping      |
| `struct x25_skb_cb`| Per-skb control block — flags, LCN                         |
| `struct x25_facilities` | Negotiated VC parameters (window, packet size, etc.)  |
| `struct x25_address` | X.121 address (up to 15 BCD digits)                      |

## Key Functions

| Function              | Description                                          |
|-----------------------|------------------------------------------------------|
| `x25_rcv()`           | Main packet receive — dispatch by packet type        |
| `x25_sendmsg()`       | Send data on an X.25 virtual circuit                 |
| `x25_recvmsg()`       | Receive data from an X.25 virtual circuit            |
| `x25_connect()`       | Initiate a Switched Virtual Circuit                  |
| `x25_accept()`        | Accept an incoming virtual circuit call              |
| `x25_create()`        | Create an AF_X25 socket                              |
| `x25_release()`       | Close socket and clear virtual circuit               |
| `x25_route_ioctl()`   | Manage X.25 routing table via ioctl                  |

## Analogy

X.25 is like the **old telephone network** applied to data. When you make a
call (`CALL REQUEST`), the network sets up a dedicated path (virtual circuit)
with a line number (LCN). Data flows in order on that circuit, with the
network ensuring no packets are lost (flow control via P(S)/P(R) counters).
When you hang up (`CLEAR REQUEST`), the circuit is torn down and the line
number is freed. Unlike modern IP (which is like sending postcards that might
arrive out of order), X.25 guarantees ordered, reliable delivery on each
circuit.

## Source Files

| File                 | Purpose                                |
|----------------------|----------------------------------------|
| `net/x25/af_x25.c`  | Core AF_X25 socket implementation      |
| `net/x25/x25_in.c`  | Incoming packet processing             |
| `net/x25/x25_out.c` | Outgoing packet construction           |
| `net/x25/x25_route.c` | Routing table management             |
| `net/x25/x25_facilities.c` | Facility negotiation              |
| `net/x25/x25_link.c` | LAPB link layer interface             |
| `net/x25/x25_subr.c` | Utility routines                      |
