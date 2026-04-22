# MCTP — Management Component Transport Protocol

## Overview

**MCTP (Management Component Transport Protocol)** is a low-level system
management messaging protocol standardized by DMTF (DSP0236).  It is designed
to connect **baseboard management controllers (BMC), CPUs, PCIe endpoint
devices, I2C devices, and USB devices** on a server's management plane.

Linux MCTP support (merged in 5.15) provides:
- `AF_MCTP` socket family — POSIX messaging API for management software
- Multiple physical transport bindings: USB, PCIe VDM, I2C/SMBus, SPI, serial
- Per-hop routing with a 1-byte endpoint ID (EID) address space per segment
- Fragmentation / reassembly for large messages
- Tag-based flow control (3-bit tag per destination)
- Type-dispatch: each message type (PLDM, NCSI, OEM, …) bound to a socket

Source: `net/mctp/`, `include/uapi/linux/mctp.h`, `include/net/mctp.h`.

---

## Subsystem Stack

```
┌────────────────────────────────────────────────────────────────┐
│                        USERSPACE                               │
│  OpenBMC / pldmd / mctp-ctrl                                   │
│  socket(AF_MCTP, SOCK_DGRAM, 0)                               │
│  bind(sockfd, {AF_MCTP, net, eid, type}, sizeof(...))         │
│  sendmsg / recvmsg                                            │
└──────────────────────────────┬─────────────────────────────────┘
                               │  syscall
┌──────────────────────────────▼─────────────────────────────────┐
│               AF_MCTP SOCKET LAYER  (af_mctp.c)                │
│                                                                 │
│  struct mctp_sock (embeds struct sock)                         │
│  bind: registers EID + message-type → socket mapping          │
│  sendmsg: builds MCTP header + fragment if needed → route     │
│  recvmsg: dequeues from sk_receive_queue                      │
│                                                                 │
│  Key: mctp_sk_key — per-socket tag allocation table           │
│  Key expiry timer: cleans up tags after timeout               │
└──────────────────────────────┬─────────────────────────────────┘
                               │
┌──────────────────────────────▼─────────────────────────────────┐
│               MCTP ROUTING  (route.c)                          │
│                                                                 │
│  mctp_route: nexthop device + output function per EID range    │
│  mctp_route_add_local() — local EID → deliver to socket        │
│  mctp_route_add() — remote EID → forward to dev                │
│  Fragmentation: large messages split into MTU-sized packets    │
│  Reassembly: fragments collected by (src_eid, tag) key         │
└──────────────────────────────┬─────────────────────────────────┘
                               │
┌──────────────────────────────▼─────────────────────────────────┐
│               MCTP DEVICE  (device.c)                          │
│                                                                 │
│  struct mctp_dev — per-interface MCTP configuration           │
│  EID assignment, network ID, physical addressing               │
│  mctp_alloc_local_tag() — allocate a message tag               │
│  Neighbor table (neigh.c): ARP-like EID → physical address     │
└──────────────────────────────┬─────────────────────────────────┘
                               │  struct sk_buff
┌──────────────────────────────▼─────────────────────────────────┐
│               TRANSPORT BINDING DRIVERS                        │
│  drivers/net/mctp/mctp-i2c.c  — SMBus/I2C binding             │
│  drivers/usb/class/cdc-mctp.c — USB CDC MCTP binding          │
│  drivers/net/mctp/mctp-pcc.c  — Platform Communication Channel│
│  drivers/net/mctp/mctp-serial.c — serial (RFC-style framing)  │
│  drivers/pci/endpoint/functions/pci-epf-mhi.c — PCIe VDM      │
└────────────────────────────────────────────────────────────────┘
```

---

## MCTP Header Format

```
  Byte 0:   Version (always 0x01)
  Byte 1:   Destination EID
  Byte 2:   Source EID
  Byte 3:   [SOM|EOM|PktSeq|TO|MsgTag] flags + tag
  Byte 4+:  Payload (message type + body)
```

- **EID** — 8-bit endpoint identifier; 0 = NULL, 1 = broadcast, 8-255 = valid
- **SOM/EOM** — first/last fragment flags
- **TO (Tag Owner)** — 1 = initiator of this exchange owns the tag
- **MsgTag** — 3-bit conversation ID within a source/dest pair

---

## Key Data Structures

| Structure | Purpose |
|---|---|
| `mctp_sock` | Per-socket: bound EID, message type, tag table |
| `mctp_sk_key` | Per-conversation tag allocation entry |
| `mctp_route` | Routing entry: EID range → output device + function |
| `mctp_dev` | Per-interface MCTP config (EIDs, net ID, physical addr) |
| `mctp_neigh` | EID → physical address mapping (neighbour table) |
| `mctp_hdr` | On-wire MCTP header |

---

## Key Source Files

| File | Purpose |
|---|---|
| `net/mctp/af_mctp.c` | Socket layer: bind, sendmsg, recvmsg |
| `net/mctp/route.c` | Routing + fragmentation/reassembly |
| `net/mctp/device.c` | MCTP device/interface management |
| `net/mctp/neigh.c` | Neighbour (EID → physical addr) table |
| `include/uapi/linux/mctp.h` | UAPI: sockaddr_mctp, message types |
| `include/net/mctp.h` | Internal structures |

---

## Analogy

MCTP is like **house numbers on a very small street** (the server management bus):

- Each component on the bus (BMC, CPU, PCIe device) gets a short **EID**
  (like a house number, 8–255).
- A **socket** is a mailbox at one of those houses that only accepts letters
  of a specific type (e.g., only PLDM letters, or only OEM letters).
- The **router** is the postman who knows which physical bus each house is on
  and delivers accordingly (including splitting large parcels into
  MTU-sized pieces and reassembling them at the destination).
- **Tags** are like return address tracking numbers: both sides agree on a
  3-bit number for the duration of a conversation, so replies don't get mixed up.

---

## References

- `include/uapi/linux/mctp.h` — socket API
- `net/mctp/` — full implementation
- DMTF DSP0236 — MCTP Base Specification
- `Documentation/networking/mctp.rst`
