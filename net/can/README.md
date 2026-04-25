# CAN — Controller Area Network

## Overview

**CAN (Controller Area Network)** is a robust bus protocol originally
designed for automotive and industrial control systems.  The Linux kernel's
`net/can/` subsystem exposes CAN via the **AF_CAN** socket family, providing
multiple protocol layers:

- **Raw CAN** (`CAN_RAW`) — direct frame send/receive on CAN interfaces
- **BCM** (`CAN_BCM`) — Broadcast Manager for periodic TX and content-filtered RX
- **ISO-TP** (`CAN_ISOTP`) — transport protocol for segmented messages (ISO 15765-2)
- **J1939** (`CAN_J1939`) — SAE J1939 transport for heavy-duty vehicles
- **GW** — CAN gateway for frame routing between CAN buses

CAN uses short fixed-length frames (8 bytes classic, 64 bytes CAN FD)
identified by an arbitration ID rather than addresses.

Source: `net/can/`, `include/linux/can/`, `include/uapi/linux/can.h`.

---

## Subsystem Stack

```
┌──────────────────────────────────────────────────────────────────┐
│                        USER SPACE                                │
│  socket(AF_CAN, SOCK_RAW, CAN_RAW)                              │
│  socket(AF_CAN, SOCK_DGRAM, CAN_BCM)                            │
│  socket(AF_CAN, SOCK_DGRAM, CAN_ISOTP)                          │
│  socket(AF_CAN, SOCK_DGRAM, CAN_J1939)                          │
└──────────────────────────────┬───────────────────────────────────┘
                               │ sendmsg / recvmsg
┌──────────────────────────────▼───────────────────────────────────┐
│               CAN PROTOCOL HANDLERS  (net/can/)                  │
│                                                                   │
│  can_create()        — socket creation, protocol dispatch        │
│  can_send()          — enqueue CAN frame to netdev               │
│  can_rcv()           — receive path, deliver to matching sockets  │
│  can_rx_register()   — register CAN ID filter for reception      │
│                                                                   │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐            │
│  │ CAN_RAW  │ │ CAN_BCM  │ │ CAN_ISOTP│ │ CAN_J1939│            │
│  │ raw_rcv  │ │bcm_sendmsg│ │isotp_rcv │ │j1939_send│            │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘            │
└──────────────────────────────┬───────────────────────────────────┘
                               │ dev_queue_xmit
┌──────────────────────────────▼───────────────────────────────────┐
│                    CAN DEVICE DRIVERS                             │
│                                                                   │
│  vcan  — virtual CAN for testing (loopback)                      │
│  slcan — Serial Line CAN (UART ↔ CAN)                           │
│  peak_usb / socketcan — hardware CAN adapters                    │
│  mcp251x / mcp251xfd — SPI-attached CAN controllers             │
│                                                                   │
│  struct can_frame   { canid_t can_id; __u8 data[8]; }           │
│  struct canfd_frame { canid_t can_id; __u8 data[64]; }          │
└──────────────────────────────────────────────────────────────────┘
```

---

## CAN Frame Format

```
Classic CAN frame (struct can_frame):
┌──────────────┬───────┬──────────────────────────────┐
│  can_id (32) │len (1)│  data[8]                     │
│  [11/29-bit  │       │  (0-8 bytes payload)         │
│   arb ID +   │       │                              │
│   EFF/RTR/   │       │                              │
│   ERR flags] │       │                              │
└──────────────┴───────┴──────────────────────────────┘

CAN FD frame (struct canfd_frame):
┌──────────────┬───────┬──────┬───────────────────────┐
│  can_id (32) │len (1)│flags │  data[64]             │
│              │       │(BRS, │  (0-64 bytes payload) │
│              │       │ ESI) │                       │
└──────────────┴───────┴──────┴───────────────────────┘
```

---

## Key Data Structures

| Structure | Purpose |
|---|---|
| `struct can_frame` | Classic CAN frame: 11/29-bit ID + up to 8 data bytes |
| `struct canfd_frame` | CAN FD frame: same ID + up to 64 data bytes |
| `struct raw_sock` | Per-socket state for CAN_RAW protocol |
| `struct bcm_sock` | Per-socket state for BCM: TX/RX timer jobs |
| `struct isotp_sock` | ISO-TP session: segmentation/reassembly state |
| `struct j1939_session` | J1939 transport session for multi-packet messages |
| `struct can_dev_rcv_lists` | Per-device receive filter lists (SFF/EFF/masks) |

---

## Key Functions

| Function | Role |
|---|---|
| `can_create()` | AF_CAN socket creation, protocol selection |
| `can_send()` | Transmit CAN frame: validate, clone, dev_queue_xmit |
| `can_rcv()` | Receive CAN frame: match filters, deliver to sockets |
| `can_rx_register()` | Register a CAN ID filter for frame reception |
| `raw_rcv()` | CAN_RAW receive callback: copy frame to socket buffer |
| `bcm_sendmsg()` | BCM message handler: set up TX/RX timer jobs |
| `isotp_rcv()` | ISO-TP receive: reassemble segmented frames |
| `j1939_send_one()` | J1939 single-frame or BAM/TP transmit |
| `vcan_tx()` | Virtual CAN transmit: loopback to local receivers |

---

## Key Source Files

| File | Purpose |
|---|---|
| `net/can/af_can.c` | AF_CAN socket family, can_rcv, can_send |
| `net/can/raw.c` | CAN_RAW protocol: raw_rcv, raw_sendmsg |
| `net/can/bcm.c` | CAN_BCM protocol: periodic TX/RX manager |
| `net/can/isotp.c` | CAN_ISOTP: segmentation and reassembly |
| `net/can/j1939/` | SAE J1939 transport protocol |
| `drivers/net/can/vcan.c` | Virtual CAN loopback interface |
| `include/uapi/linux/can.h` | CAN frame and socket definitions |

---

## Analogy

CAN is like a **shared walkie-talkie channel on a factory floor**:

- Everyone hears every message (broadcast bus), but each listener has a
  **filter card** (CAN ID filter) and only picks up messages they care about.
- Messages are short — like a quick radio call: "Station 0x123: temperature
  is 42°C" — just an ID and a few bytes of data.
- **CAN_RAW** is listening to the raw channel.  **BCM** is a secretary who
  logs periodic check-ins and alerts you only when values change.
  **ISO-TP** lets you send a long document by breaking it into walkie-talkie-
  sized pieces and reassembling at the other end.  **J1939** is the heavy-
  truck dialect that names stations by their function (engine, brakes).

---

## References

- `net/can/af_can.c` — core AF_CAN implementation
- `include/uapi/linux/can.h` — UAPI structures
- Linux kernel CAN documentation: `Documentation/networking/can.rst`
- ISO 11898 — CAN standard
- ISO 15765-2 — ISO-TP transport protocol
- SAE J1939 — heavy-duty vehicle bus
