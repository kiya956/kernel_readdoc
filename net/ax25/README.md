# Linux Kernel net/ax25 — AX.25 Amateur Radio Protocol

## Overview

**AX.25** implements the amateur radio packet protocol in the Linux kernel,
providing the `AF_AX25` socket family. AX.25 is the data link layer protocol
used by ham radio operators for packet radio networking. The implementation
supports connected mode (virtual circuits), connectionless datagrams, and
serves as the foundation for NET/ROM and ROSE upper-layer protocols.

Source: `net/ax25/`, `include/net/ax25.h`.

---

## Subsystem Stack

```
┌─────────────────────────────────────────────────────────────────┐
│                        USERSPACE                                │
│                                                                 │
│  socket(AF_AX25, ...)      ax25d daemon     call tool           │
│  packet radio apps         NET/ROM / ROSE upper layers          │
└───────────────────────────────┬─────────────────────────────────┘
                                │ AF_AX25 sockets
┌───────────────────────────────▼─────────────────────────────────┐
│                   AX.25 SOCKET LAYER                             │
│                   (net/ax25/af_ax25.c)                           │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  struct ax25_cb  (AX.25 control block)                  │   │
│  │  - source / dest callsign                               │   │
│  │  - state (connected/listening/etc.)                     │   │
│  │  - digipeater path                                      │   │
│  │  - T1/T2/T3/N2 timers                                  │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  ax25_sendmsg()  — send data frame                             │
│  ax25_recvmsg()  — receive data frame                          │
└───────────────────────────────┬─────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────┐
│                   AX.25 PROTOCOL ENGINE                          │
│                                                                 │
│  ┌──────────────┐ ┌───────────────┐ ┌──────────────────────┐   │
│  │  ax25_in.c   │ │  ax25_out.c   │ │  ax25_timer.c        │   │
│  │  Input frame │ │  Output frame │ │  T1 retransmit       │   │
│  │  processing  │ │  queuing      │ │  T2 ack delay        │   │
│  │  (I/S/U)     │ │               │ │  T3 idle             │   │
│  └──────────────┘ └───────────────┘ └──────────────────────┘   │
│                                                                 │
│  ┌──────────────┐ ┌───────────────┐                             │
│  │  ax25_route  │ │  ax25_uid     │                             │
│  │  .c          │ │  .c           │                             │
│  │  Routing /   │ │  UID ↔ call   │                             │
│  │  digipeaters │ │  mapping      │                             │
│  └──────────────┘ └───────────────┘                             │
└───────────────────────────────┬─────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────┐
│                   AX.25 DEVICE LAYER                             │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  struct ax25_dev  (per-device AX.25 parameters)         │   │
│  │  - callsign, mode (MODULUS 8/128)                       │   │
│  │  - values[] (T1/T2/T3/N2 defaults)                     │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  ax25_rcv()  — receive handler from network device             │
└───────────────────────────────┬─────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────┐
│               RADIO HARDWARE (TNC / soundmodem / kissattach)    │
└─────────────────────────────────────────────────────────────────┘
```

---

## Workflow: AX.25 Frame Reception

```
  Radio frame received
       │
       ▼
  ax25_rcv(skb, dev, ...)
       │
       ├──► decode AX.25 header (callsigns, control)
       ├──► ax25_find_cb()    find matching connection
       ├──► ax25_process_rx_frame()
       │         │
       │    ┌────┴────────────────────────────┐
       │    │ I-frame:  queue data to socket   │
       │    │ S-frame:  flow control (RR/RNR)  │
       │    │ U-frame:  connect/disconnect     │
       │    └─────────────────────────────────┘
       └──► sock_queue_rcv_skb()   deliver to app
```

---

## Key Data Structures

| Structure | Purpose |
|---|---|
| `struct ax25_cb` | AX.25 control block — connection state machine |
| `struct ax25_dev` | Per-device AX.25 parameters and callsign |
| `struct ax25_route` | Routing table entry (digipeater paths) |
| `struct ax25_uid_assoc` | UID to callsign mapping |
| `struct ax25_addr` | AX.25 callsign address (6 chars + SSID) |

## Key Functions

| Function | Purpose |
|---|---|
| `ax25_rcv()` | Receive incoming AX.25 frames from device |
| `ax25_sendmsg()` | Transmit data via AX.25 socket |
| `ax25_recvmsg()` | Receive data from AX.25 socket |
| `ax25_connect()` | Establish connected-mode link |
| `ax25_create()` | Create AF_AX25 socket |
| `ax25_rt_find_route()` | Look up digipeater route |

## Key Source Files

| File | Purpose |
|---|---|
| `net/ax25/af_ax25.c` | Socket layer, create/bind/connect |
| `net/ax25/ax25_in.c` | Incoming frame processing |
| `net/ax25/ax25_out.c` | Outgoing frame queuing |
| `net/ax25/ax25_timer.c` | Protocol timers (T1/T2/T3) |
| `net/ax25/ax25_route.c` | Digipeater routing |
| `net/ax25/ax25_dev.c` | Device management |
| `include/net/ax25.h` | Core data structures |

---

## Analogy

AX.25 is like **walkie-talkie messaging with delivery confirmation**:

- Each **station** has a callsign (like a radio handle: "KB1ABC-2").
- **Connected mode** is like opening a dedicated radio channel with someone —
  you take turns talking, acknowledge each message, and retransmit if not heard.
- **Digipeaters** are relay stations — your message hops through them like
  passing a note through friends to reach someone out of range.
- **T1/T2/T3 timers** are patience timers — if you don't get a response in time,
  you repeat yourself or give up.

---

## References

- `include/net/ax25.h` — Core API and structures
- `include/uapi/linux/ax25.h` — Userspace ABI
- `net/ax25/` — Implementation
