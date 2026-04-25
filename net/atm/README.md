# Linux Kernel net/atm — Asynchronous Transfer Mode

## Overview

**ATM** (Asynchronous Transfer Mode) implements cell-based switching and
ATM socket support in the Linux kernel. ATM divides data into fixed 53-byte
cells for high-speed WAN switching. The Linux implementation includes the
ATM socket layer (`PF_ATMPVC`, `PF_ATMSVC`), signaling (Q.2931), LAN
Emulation (LANE), Multi-Protocol Over ATM (MPOA), and AAL5 adaptation.

Source: `net/atm/`, `include/linux/atm*.h`.

---

## Subsystem Stack

```
┌─────────────────────────────────────────────────────────────────┐
│                        USERSPACE                                │
│                                                                 │
│  atmarpd        ilmid           lecs/les/bus     mpcd           │
│  PVC sockets    SVC sockets     LANE clients     MPOA clients   │
└───────────────────────────────┬─────────────────────────────────┘
                                │ PF_ATMPVC / PF_ATMSVC sockets
┌───────────────────────────────▼─────────────────────────────────┐
│                   ATM SOCKET LAYER                               │
│                   (net/atm/common.c, pvc.c, svc.c)              │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  struct atm_vcc (virtual channel connection)            │   │
│  │  - vpi/vci          (virtual path/channel identifiers)  │   │
│  │  - qos              (traffic parameters)                │   │
│  │  - dev              (associated ATM device)             │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  vcc_sendmsg()  — send data over ATM VCC                       │
│  vcc_recvmsg()  — receive data from ATM VCC                    │
└───────────────────────────────┬─────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────┐
│                   ATM PROTOCOL LAYERS                            │
│                                                                 │
│  ┌──────────────┐ ┌───────────────┐ ┌──────────────────────┐   │
│  │  LANE        │ │  MPOA         │ │  Classical IP        │   │
│  │  (lec.c)     │ │  (mpoa*.c)    │ │  (clip.c)           │   │
│  │  LAN         │ │  Multi-       │ │  RFC 2225            │   │
│  │  Emulation   │ │  Protocol     │ │  IP over ATM         │   │
│  └──────────────┘ └───────────────┘ └──────────────────────┘   │
│                                                                 │
│  ┌──────────────┐ ┌───────────────┐                             │
│  │  Signaling   │ │  AAL5         │                             │
│  │  (signaling  │ │  (common.c)   │                             │
│  │  .c, svc.c)  │ │  SAR layer    │                             │
│  └──────────────┘ └───────────────┘                             │
└───────────────────────────────┬─────────────────────────────────┘
                                │ atm_dev->ops callbacks
┌───────────────────────────────▼─────────────────────────────────┐
│                   ATM DEVICE DRIVERS                             │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  struct atm_dev  (per-device state)                     │   │
│  │  - ops (open, close, send, ioctl)                       │   │
│  │  - type, number                                         │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  fore200e, eni, idt77252, he, nicstar, …                       │
└───────────────────────────────┬─────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────┐
│               ATM HARDWARE (switches / DSLAMs)                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Workflow: ATM Send Path

```
  sendmsg(sock, msg, ...)
       │
       ▼
  vcc_sendmsg(sock, msg, size)
       │
       ├──► alloc_skb()             allocate buffer
       ├──► copy_from_user()        copy userspace data
       ├──► vcc->dev->ops->send()   driver transmit
       │         │
       │         ▼
       │    AAL5 segmentation → 53-byte ATM cells
       │         │
       └─────────▼
            hardware transmit
```

---

## Key Data Structures

| Structure | Purpose |
|---|---|
| `struct atm_vcc` | Virtual channel connection (socket↔circuit) |
| `struct atm_dev` | ATM network device (driver instance) |
| `struct atm_qos` | Quality of service parameters (CBR/VBR/UBR) |
| `struct sockaddr_atmpvc` | PVC socket address (vpi:vci) |
| `struct sockaddr_atmsvc` | SVC socket address (ATM E.164/NSAP) |

## Key Functions

| Function | Purpose |
|---|---|
| `vcc_sendmsg()` | Send data over an ATM VCC |
| `vcc_recvmsg()` | Receive data from an ATM VCC |
| `atm_init_aal5()` | Initialize AAL5 adaptation layer |
| `vcc_connect()` | Establish VCC connection |
| `atm_dev_register()` | Register ATM hardware device |
| `clip_push()` | Classical IP over ATM receive |

## Key Source Files

| File | Purpose |
|---|---|
| `net/atm/common.c` | Core ATM socket operations |
| `net/atm/pvc.c` | Permanent Virtual Circuit sockets |
| `net/atm/svc.c` | Switched Virtual Circuit sockets |
| `net/atm/signaling.c` | Q.2931 signaling interface |
| `net/atm/lec.c` | LAN Emulation client |
| `net/atm/clip.c` | Classical IP over ATM |
| `net/atm/mpoa_caches.c` | MPOA cache management |
| `include/linux/atmdev.h` | Device driver API |

---

## Analogy

ATM is like a **high-speed conveyor belt with fixed-size boxes**:

- Each **ATM cell** (53 bytes) is a standardized box — no matter how big
  your shipment, it gets divided into identical boxes.
- A **VCC** (Virtual Channel Connection) is a dedicated lane on the conveyor,
  identified by VPI:VCI labels — your boxes always follow the same lane.
- **AAL5** is the packing service — it takes your large parcel, cuts it into
  cell-sized pieces, and reassembles them at the destination.
- The **ATM switch** reads the lane labels and routes each box to the right
  output port at wire speed.

---

## References

- `include/linux/atmdev.h` — Device driver interface
- `include/uapi/linux/atm.h` — Userspace ATM ABI
- `net/atm/` — Implementation
