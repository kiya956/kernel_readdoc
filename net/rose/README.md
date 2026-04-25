# ROSE — X.25 over AX.25 Amateur Radio Protocol

## Overview

The ROSE subsystem implements the ROSE (Radiocom Online System for Education)
protocol in the Linux kernel. ROSE provides an X.25-like network layer that runs
over AX.25 amateur radio data links, enabling virtual circuit-based communication
between amateur radio stations.

Key features:

- **X.25 compatible** — Uses X.25 packet layer procedures adapted for radio
- **Virtual circuits** — Connection-oriented reliable delivery
- **10-digit addressing** — DNIC (4 digits) + station address (6 digits)
- **AF_ROSE sockets** — BSD socket interface for applications
- **Routing** — Static routes through ROSE switches/nodes

ROSE is part of the amateur packet radio ecosystem, alongside AX.25 and NET/ROM.

## Kernel Source

- **Directory:** `net/rose/`
- **Headers:** `include/net/rose.h`
- **Config:** `CONFIG_ROSE`

## Architecture

```
┌─────────────────────────────────────────────┐
│         User Space Applications             │
│      (node, call, BBS, DX cluster)         │
├─────────────────────────────────────────────┤
│           AF_ROSE Socket Layer              │
│     rose_sendmsg / rose_recvmsg            │
├─────────────────────────────────────────────┤
│           ROSE Protocol Engine              │
│  ┌────────────────┬─────────────────┐       │
│  │  Virtual       │   Routing       │       │
│  │  Circuit Mgmt  │   Table         │       │
│  │  rose_sock     │   rose_node     │       │
│  │  Call setup/   │   rose_neigh    │       │
│  │  clear/reset   │   rose_route    │       │
│  └────────────────┴─────────────────┘       │
├─────────────────────────────────────────────┤
│         ROSE Packet Layer                   │
│   Call Request/Accept/Clear/Data/RR/RNR    │
├─────────────────────────────────────────────┤
│            AX.25 Data Link                  │
│     ax25_send_frame / ax25_rcv             │
├─────────────────────────────────────────────┤
│         Radio Hardware (TNC/Modem)          │
│     KISS, 6PACK, SCC, baycom               │
└─────────────────────────────────────────────┘
```

## Packet Flow

```
 SEND PATH (Data)                       RECEIVE PATH
 ────────────────                       ────────────

 User application                       AX.25 delivers frame
     │                                       │
     ▼                                       ▼
 rose_sendmsg()                         rose_rcv()
     │                                       │
     ▼                                       ▼
 Build ROSE data packet                 Parse ROSE header
 (GFI, LCI, data)                       (packet type, LCI)
     │                                       │
     ▼                                  ┌────┴──────┐
 rose_kick()                           │           │
     │                             For us?      Forward?
     ▼                                  │           │
 rose_send_iframe()                     ▼           ▼
     │                            rose_process  rose_route
     ▼                            _rx_frame     _frame
 AX.25 send via                         │           │
 neighbor route                         ▼           ▼
     │                            Socket recv   Next-hop TX
     ▼
 Radio TX

 CONNECTION SETUP
 ────────────────
 rose_connect() → Call Request → Remote → Call Accept → Connected
```

## Key Structures

| Structure | File | Purpose |
|-----------|------|---------|
| `struct rose_sock` | `include/net/rose.h` | Per-connection virtual circuit state |
| `struct rose_node` | `include/net/rose.h` | Routing table — destination ROSE address |
| `struct rose_neigh` | `include/net/rose.h` | Neighbor entry — adjacent ROSE switch |
| `struct rose_route` | `include/net/rose.h` | Active route for virtual circuit |
| `struct rose_facilities` | `include/net/rose.h` | X.25 facilities (window, packet size) |

## Key Functions

| Function | File | Purpose |
|----------|------|---------|
| `rose_rcv()` | `net/rose/rose_in.c` | Main receive — dispatch incoming frames |
| `rose_sendmsg()` | `net/rose/af_rose.c` | Socket sendmsg for AF_ROSE |
| `rose_recvmsg()` | `net/rose/af_rose.c` | Socket recvmsg for AF_ROSE |
| `rose_connect()` | `net/rose/af_rose.c` | Initiate virtual circuit (Call Request) |
| `rose_route_frame()` | `net/rose/rose_route.c` | Route/forward ROSE packet |
| `rose_kick()` | `net/rose/rose_out.c` | Transmit queued data frames |

## Analogy

ROSE is like a **telephone switching network over radio**. When you want to "call"
a distant station, you dial their 10-digit ROSE number. The network sets up a
virtual circuit — a dedicated path through relay switches — just like an old
telephone exchange connecting your call through a series of switchboards. Once
connected, data flows reliably along this path. When done, you "hang up" (clear
the circuit). Unlike NET/ROM's hop-by-hop routing, ROSE creates an end-to-end
circuit first, then sends data along it.
