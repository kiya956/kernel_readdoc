# Phonet — Nokia ISI Messaging Protocol

## Overview

The Phonet subsystem implements the Nokia Phonet protocol in the Linux kernel.
Phonet is an inter-process communication protocol originally designed for Nokia's
cellular modem architecture, used for ISI (Intelligent Service Interface) messaging
between the application processor and the cellular modem.

Key features:

- **AF_PHONET sockets** — BSD socket interface for Phonet communication
- **Resource routing** — Messages routed by 8-bit resource IDs
- **Pipe protocol** — Connection-oriented reliable pipes (PEP)
- **Device multiplexing** — Multiple Phonet devices (USB, HSI, SSI)

Phonet is used primarily on Nokia/Qualcomm platforms where the modem runs a
separate processor and communicates via ISI messages.

## Kernel Source

- **Directory:** `net/phonet/`
- **Headers:** `include/net/phonet/phonet.h`, `include/net/phonet/pep.h`
- **Config:** `CONFIG_PHONET`

## Architecture

```
┌─────────────────────────────────────────────┐
│         User Space (ofono, libisi)          │
│     AF_PHONET / AF_PHONET + PHONET_PIPE    │
├─────────────────────────────────────────────┤
│         Phonet Socket Layer                 │
│  ┌──────────────┬───────────────────┐       │
│  │  Datagram    │   Pipe (PEP)      │       │
│  │  pn_sendmsg  │   pep_sendmsg     │       │
│  │  Resource ID │   Connection-     │       │
│  │  routing     │   oriented pipes  │       │
│  └──────────────┴───────────────────┘       │
├─────────────────────────────────────────────┤
│          Phonet Core                        │
│   phonet_rcv, resource table, routing      │
├─────────────────────────────────────────────┤
│        Phonet Device Interface              │
│   phonet_device (net_device based)         │
├─────────────────────────────────────────────┤
│      Transport (USB/HSI/SSI/SPI)            │
│   cdc-phonet, hsi_char, n_gsm              │
└─────────────────────────────────────────────┘
```

## Packet Flow

```
 SEND PATH                              RECEIVE PATH
 ─────────                              ────────────

 User application                       Modem sends ISI message
     │                                       │
     ▼                                       ▼
 pn_sendmsg()                           phonet_rcv()
  or pep_sendmsg()                           │
     │                                       ▼
     ▼                                  Parse Phonet header
 Build Phonet header                    (src/dst device, resource)
 (media, rdev, sdev,                         │
  res, obj)                             ┌────┴─────┐
     │                                  │          │
     ▼                               Datagram?   Pipe?
 phonet_device_xmit()                   │          │
     │                                  ▼          ▼
     ▼                              pn_rcv     pep_rcv
 dev_queue_xmit()                   resource   pipe
     │                              lookup     lookup
     ▼                                  │          │
 USB/HSI/SSI TX                         ▼          ▼
                                  Socket recv  Socket recv
```

## Key Structures

| Structure | File | Purpose |
|-----------|------|---------|
| `struct phonet_device` | `include/net/phonet/phonet.h` | Per net_device Phonet state |
| `struct pn_sock` | `include/net/phonet/phonet.h` | Phonet datagram socket state |
| `struct pep_sock` | `include/net/phonet/pep.h` | Phonet pipe endpoint socket |
| `struct phonethdr` | `include/linux/phonet.h` | Phonet message header |

## Key Functions

| Function | File | Purpose |
|----------|------|---------|
| `phonet_rcv()` | `net/phonet/af_phonet.c` | Main receive — route incoming Phonet frames |
| `pn_sendmsg()` | `net/phonet/datagram.c` | Send datagram via Phonet |
| `pn_recvmsg()` | `net/phonet/datagram.c` | Receive datagram from Phonet |
| `pep_sendmsg()` | `net/phonet/pep.c` | Send data over Phonet pipe |
| `pep_recvmsg()` | `net/phonet/pep.c` | Receive data from Phonet pipe |
| `phonet_device_register()` | `net/phonet/pn_dev.c` | Register a Phonet-capable device |

## Analogy

Phonet is like a **building's internal phone system** connecting offices (application
processor services) to the front desk (cellular modem). Each office has an extension
number (resource ID). Datagram mode is like leaving a voicemail — fire and forget.
Pipe mode (PEP) is like an active phone call — a dedicated connection where both
sides talk back and forth. The PBX switchboard (Phonet core) routes calls to the
right extension based on the number dialed.
