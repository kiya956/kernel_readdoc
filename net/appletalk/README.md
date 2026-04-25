# Linux Kernel net/appletalk — AppleTalk DDP Protocol

## Overview

**AppleTalk** implements the **Datagram Delivery Protocol (DDP)** in the Linux
kernel, providing legacy Apple networking via the `AF_APPLETALK` socket family.
AppleTalk was Apple's original networking suite, supporting services like
printer discovery and file sharing. The Linux implementation supports DDP
over EtherTalk (ELAP) and LocalTalk (LLAP) encapsulations.

Source: `net/appletalk/`, `include/linux/atalk.h`.

---

## Subsystem Stack

```
┌─────────────────────────────────────────────────────────────────┐
│                        USERSPACE                                │
│                                                                 │
│  socket(AF_APPLETALK, SOCK_DGRAM, 0)                           │
│  netatalk daemon         atalkd          papd / afpd            │
└───────────────────────────────┬─────────────────────────────────┘
                                │ syscall: sendmsg/recvmsg
┌───────────────────────────────▼─────────────────────────────────┐
│                   AF_APPLETALK SOCKET LAYER                     │
│                   (net/appletalk/ddp.c)                          │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  struct atalk_sock  (per-socket state)                  │   │
│  │  - src_net, src_node, src_port                          │   │
│  │  - dest_net, dest_node, dest_port                       │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  ddp_sendmsg()  — build DDP header, route, transmit            │
│  ddp_recvmsg()  — dequeue from socket receive queue            │
└───────────────────────────────┬─────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────┐
│                   DDP ROUTING LAYER                              │
│                   (net/appletalk/ddp.c)                          │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  struct atalk_route  (routing table entry)              │   │
│  │  - target network range                                 │   │
│  │  - gateway node / device                                │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  atrtr_find()  — lookup route for destination network          │
└───────────────────────────────┬─────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────┐
│                   PACKET RX/TX                                   │
│                                                                 │
│  atalk_rcv()   — receive handler registered with dev_add_pack  │
│  ltalk_rcv()   — LocalTalk receive handler                     │
│                                                                 │
│  EtherTalk: ETH_P_ATALK (0x809B) via SNAP/802.2               │
└───────────────────────────────┬─────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────┐
│               NETWORK DEVICE (Ethernet / LocalTalk)             │
└─────────────────────────────────────────────────────────────────┘
```

---

## Workflow: DDP Packet Send

```
  sendmsg(sock, msg, ...)
       │
       ▼
  ddp_sendmsg()
       │
       ├──► build DDP header (src/dst net:node:port)
       ├──► atrtr_find()          lookup route
       ├──► skb_push()            prepend DDP header
       └──► dev_queue_xmit()      transmit via net device
            │
            ▼
       Ethernet frame with EtherTalk encapsulation
```

---

## Key Data Structures

| Structure | Purpose |
|---|---|
| `struct atalk_sock` | Per-socket AppleTalk state (address, port) |
| `struct atalk_route` | DDP routing table entry |
| `struct atalk_iface` | AppleTalk interface (network range, node) |
| `struct ddpehdr` | DDP extended header (wire format) |

## Key Functions

| Function | Purpose |
|---|---|
| `atalk_rcv()` | Receive incoming DDP packets from network |
| `ddp_sendmsg()` | Send DDP datagram from userspace socket |
| `ddp_recvmsg()` | Receive DDP datagram to userspace |
| `atrtr_find()` | Look up routing table for destination |
| `atalk_create()` | Create AF_APPLETALK socket |
| `aarp_send_query()` | AARP address resolution |

## Key Source Files

| File | Purpose |
|---|---|
| `net/appletalk/ddp.c` | Core DDP protocol, socket ops |
| `net/appletalk/aarp.c` | AppleTalk ARP (address resolution) |
| `net/appletalk/sysctl_net_atalk.c` | Sysctl parameters |
| `include/linux/atalk.h` | AppleTalk data structures |
| `include/uapi/linux/atalk.h` | Userspace ABI |

---

## Analogy

AppleTalk is like an **old-fashioned office intercom system**:

- Each **node** on the network is like a desk with a numbered extension.
- A **DDP datagram** is like a short voice message sent to a specific
  extension — no connection setup, just send and hope they hear it.
- **AARP** (AppleTalk ARP) is the office directory — it maps extension numbers
  to physical desk locations (MAC addresses).
- **Zones** are like departments — you can ask "who's in Engineering?" to
  discover services.

---

## References

- `include/linux/atalk.h` — Core data structures
- `include/uapi/linux/atalk.h` — Userspace ABI
- `net/appletalk/` — Implementation
