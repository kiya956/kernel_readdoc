# Linux Kernel net/caif — ST-Ericsson Mobile Platform IPC

## Overview

**CAIF** (Communication CPU to Application CPU Interface) is a protocol used
by ST-Ericsson mobile platforms for inter-processor communication between the
modem CPU and the application CPU. The Linux implementation provides a socket
interface (`AF_CAIF`), a layered protocol stack with channels for different
services (AT commands, packet data, debug, RFM), and transport backends
(serial, SPI, HSI, shared memory).

Source: `net/caif/`, `include/net/caif/`.

---

## Subsystem Stack

```
┌─────────────────────────────────────────────────────────────────┐
│                        USERSPACE                                │
│                                                                 │
│  socket(AF_CAIF, SOCK_SEQPACKET, CAIFPROTO_AT)                 │
│  RIL daemon         caif_test         data connections          │
└───────────────────────────────┬─────────────────────────────────┘
                                │ AF_CAIF sockets
┌───────────────────────────────▼─────────────────────────────────┐
│                   CAIF SOCKET LAYER                              │
│                   (net/caif/caif_socket.c)                       │
│                                                                 │
│  caif_connect_client()  — connect to CAIF service channel      │
│  caif_sendmsg()         — send data to modem                   │
│  caif_recvmsg()         — receive data from modem              │
└───────────────────────────────┬─────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────┐
│                   CAIF PROTOCOL STACK                            │
│                   (net/caif/cfpkt_skbuff.c, cfrfml.c, …)       │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  struct cflayer  (protocol layer abstraction)           │   │
│  │  - receive()    callback                                │   │
│  │  - transmit()   callback                                │   │
│  │  - up / dn      linked layers                           │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  ┌──────────────┐ ┌───────────────┐ ┌──────────────────────┐   │
│  │  cfserl.c    │ │  cfvei.c      │ │  cfutill.c           │   │
│  │  Serial      │ │  VEI (AT cmd) │ │  Utility              │   │
│  │  framing     │ │  channel      │ │  channel              │   │
│  └──────────────┘ └───────────────┘ └──────────────────────┘   │
│  ┌──────────────┐ ┌───────────────┐                             │
│  │  cfdbgl.c    │ │  cfrfml.c     │                             │
│  │  Debug log   │ │  RFM (Remote  │                             │
│  │  channel     │ │  File Mgmt)   │                             │
│  └──────────────┘ └───────────────┘                             │
└───────────────────────────────┬─────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────┐
│                   CAIF DEVICE / TRANSPORT                        │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  struct caif_dev_common  (per net-device CAIF state)    │   │
│  │  - flowctrl_cb()        flow control callback           │   │
│  │  - link_select          high/low bandwidth link         │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  caif_device.c — netdevice notifier, link layer management     │
└───────────────────────────────┬─────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────┐
│               MODEM HARDWARE (serial / SPI / HSI / shmem)       │
└─────────────────────────────────────────────────────────────────┘
```

---

## Workflow: CAIF Client Connection

```
  socket(AF_CAIF, SOCK_SEQPACKET, CAIFPROTO_AT)
       │
       ▼
  connect(sock, addr, ...)
       │
       ▼
  caif_connect_client()
       │
       ├──► cfcnfg_add_adaptation_layer()
       │         build protocol layer chain
       │         (adaptation → mux → framing → transport)
       ├──► link cflayer stack (up/dn pointers)
       └──► send channel setup to modem
            │
            ▼
       modem acknowledges → connection ready
```

---

## Key Data Structures

| Structure | Purpose |
|---|---|
| `struct caif_dev_common` | Per-device CAIF state (flow control, link) |
| `struct cflayer` | Generic protocol layer (linked list of layers) |
| `struct cfpkt` | CAIF packet wrapper around sk_buff |
| `struct caif_connect_request` | Connection parameters (protocol, channel) |

## Key Functions

| Function | Purpose |
|---|---|
| `caif_connect_client()` | Connect socket to CAIF service channel |
| `cfpkt_fromnative()` | Convert sk_buff to CAIF packet |
| `caif_enroll_dev()` | Register CAIF-capable network device |
| `cfcnfg_add_adaptation_layer()` | Build protocol layer chain |
| `caif_sendmsg()` | Send message via CAIF socket |
| `caif_recvmsg()` | Receive message from CAIF socket |

## Key Source Files

| File | Purpose |
|---|---|
| `net/caif/caif_socket.c` | AF_CAIF socket implementation |
| `net/caif/caif_dev.c` | Device management, netdev notifier |
| `net/caif/cfpkt_skbuff.c` | Packet abstraction over sk_buff |
| `net/caif/cfcnfg.c` | Configuration and layer chain setup |
| `net/caif/cfmuxl.c` | Multiplexer layer |
| `net/caif/cfserl.c` | Serial framing layer |
| `include/net/caif/caif_dev.h` | Device API |
| `include/net/caif/cflayer.h` | Layer abstraction API |

---

## Analogy

CAIF is like a **multi-channel telephone switchboard to a modem**:

- The **application CPU** is the office, the **modem CPU** is the switchboard
  operator.
- Each **CAIF channel** is a different phone line — one for AT commands
  (voice calls), one for data, one for debug logs.
- The **cflayer stack** is the telephone wiring — each layer adds framing,
  multiplexing, and error handling as the signal passes through.
- **cfpkt_fromnative()** is putting a letter into a standardized envelope
  before handing it to the mail room.

---

## References

- `include/net/caif/caif_dev.h` — Device API
- `include/net/caif/cflayer.h` — Layer abstraction
- `net/caif/` — Implementation
