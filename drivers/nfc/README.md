# Linux Kernel NFC Subsystem

## Overview

The Linux NFC subsystem provides a unified framework for Near Field Communication
hardware. It abstracts hardware differences and exposes a socket-based API plus a
Generic Netlink control plane to userspace. The subsystem lives in `net/nfc/` (protocol
stack) and `drivers/nfc/` (hardware drivers).

---

## Subsystem Stack

```
┌─────────────────────────────────────────────────────────────────┐
│                        USERSPACE                                │
│                                                                 │
│   neard / libnfc / custom app                                   │
│        │                    │                                   │
│   AF_NFC socket          Generic Netlink (nfc)                  │
│   (SOCK_SEQPACKET /       nfc-dev-up / start-poll /             │
│    SOCK_STREAM via LLCP)   targets-found events                 │
└────────────────────────────┬────────────────────────────────────┘
                             │ syscall boundary
┌────────────────────────────▼────────────────────────────────────┐
│                    NFC SOCKET LAYER  (net/nfc/)                 │
│                                                                 │
│  ┌─────────────────────┐    ┌──────────────────────────────┐   │
│  │  af_nfc.c           │    │  netlink.c                   │   │
│  │  AF_NFC protocol    │    │  Generic Netlink family      │   │
│  │  registration       │    │  (NFC_GENL_NAME="nfc")       │   │
│  └──────────┬──────────┘    └───────────────┬──────────────┘   │
│             │                               │                   │
│  ┌──────────▼──────────┐    ┌───────────────▼──────────────┐   │
│  │  rawsock.c          │    │  llcp_sock.c + llcp_core.c   │   │
│  │  Raw NFC socket     │    │  Logical Link Control Proto  │   │
│  │  (SOCK_RAW)         │    │  (LLCP - peer-to-peer layer) │   │
│  └──────────┬──────────┘    └───────────────┬──────────────┘   │
└─────────────┼───────────────────────────────┼──────────────────┘
              │                               │
┌─────────────▼───────────────────────────────▼──────────────────┐
│                    NFC CORE  (net/nfc/core.c)                   │
│                                                                 │
│  Device management, target polling, SE management,             │
│  nfc_dev registration, rfkill integration                       │
│                                                                 │
│  Key ops: nfc_dev_up/down, nfc_start_poll, nfc_stop_poll,       │
│           nfc_activate_target, nfc_data_exchange               │
└─────────────────────────────┬───────────────────────────────────┘
                              │ nfc_ops callbacks
        ┌─────────────────────┴───────────────────┐
        │                                         │
┌───────▼──────────────────┐     ┌────────────────▼───────────────┐
│  NCI LAYER               │     │  HCI LAYER                     │
│  net/nfc/nci/            │     │  net/nfc/hci/                  │
│                          │     │                                │
│  NFC Controller Interface│     │  Host Controller Interface     │
│  (NCI 1.x spec - NFC     │     │  (ETSI TS 102 622 - older      │
│  Forum standard)         │     │   ST / Microread chips)        │
│  nci_send_cmd/recv_frame │     │  hci_send_cmd / event dispatch │
└───────┬──────────────────┘     └─────────────────┬──────────────┘
        │                                          │
┌───────▼──────────────────────────────────────────▼──────────────┐
│               DIGITAL PROTOCOL STACK (net/nfc/digital_core.c)   │
│                                                                 │
│  Software implementation of RF protocols for "dumb" adapters   │
│  (adapters that only do RF framing, no protocol intelligence)  │
│                                                                 │
│  NFC-A  ──► ISO 14443-A / Mifare / Jewel / ISO-DEP            │
│  NFC-B  ──► ISO 14443-B / ISO-DEP                             │
│  NFC-F  ──► FeliCa / NFC-DEP                                  │
│  NFC-V  ──► ISO 15693                                          │
└─────────────────────────────────────────────────────────────────┘
                              │ nfc_digital_ops callbacks
┌─────────────────────────────▼───────────────────────────────────┐
│                  HARDWARE DRIVERS  (drivers/nfc/)               │
│                                                                 │
│  NCI drivers (use nci_allocate_device):                        │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────────────┐ │
│  │ nxp-nci  │  │ nfcmrvl  │  │ st-nci   │  │ s3fwrn5        │ │
│  │ (I2C/SPI)│  │(UART/USB)│  │ (I2C/SPI)│  │ (Samsung, I2C) │ │
│  └──────────┘  └──────────┘  └──────────┘  └────────────────┘ │
│                                                                 │
│  HCI drivers (use nci_allocate_device with HCI shim):          │
│  ┌──────────────┐  ┌──────────────┐                            │
│  │  st21nfca    │  │  microread   │                            │
│  │  (ST, I2C)   │  │  (Inside,I2C)│                            │
│  └──────────────┘  └──────────────┘                            │
│                                                                 │
│  Vendor-protocol drivers (use nfc_allocate_device directly):   │
│  ┌───────────┐  ┌─────────┐  ┌──────────┐                     │
│  │  pn533    │  │ pn544   │  │ trf7970a │  port100             │
│  │(NXP,USB/  │  │(NXP,I2C)│  │(TI, SPI) │  (Sony,USB)         │
│  │  SPI/I2C) │  │         │  │          │                     │
│  └───────────┘  └─────────┘  └──────────┘                     │
└─────────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────▼───────────────────────────────────┐
│                       HARDWARE                                  │
│         NFC Controller Chip  ──►  RF Antenna  ──►  Tag/Peer     │
└─────────────────────────────────────────────────────────────────┘
```

---

## Layer-by-Layer Explanation

### 1. Userspace Interface

Two control interfaces are exposed:

| Interface | Purpose |
|---|---|
| **Generic Netlink** (`nfc` family) | Device enumeration, polling control, SE management, events |
| **AF_NFC socket** | Data exchange with discovered targets / peers |

Common netlink commands:
- `NFC_CMD_DEV_UP` / `NFC_CMD_DEV_DOWN` — power the controller on/off
- `NFC_CMD_START_POLL` / `NFC_CMD_STOP_POLL` — begin/end tag discovery
- `NFC_CMD_GET_TARGET` — enumerate found tags
- `NFC_EVENT_TARGETS_FOUND` — async notification of a new tag

### 2. AF_NFC Socket Layer (`net/nfc/af_nfc.c`)

Registers `PF_NFC` address family. Two socket types:
- **SOCK_RAW** (`rawsock.c`) — direct APDU exchange with ISO-DEP targets
- **SOCK_SEQPACKET / SOCK_STREAM** via LLCP (`llcp_sock.c`) — peer-to-peer data exchange (NFC-DEP / SNEP)

### 3. NFC Core (`net/nfc/core.c`)

Central hub. Responsibilities:
- Device registration (`nfc_register_device` / `nfc_allocate_device`)
- rfkill integration for regulatory compliance
- Target polling state machine
- Secure Element (SE) lifecycle
- Dispatches `nfc_ops` callbacks into the hardware driver

Key structures:
- `struct nfc_dev` — represents one NFC controller
- `struct nfc_ops` — driver vtable (`start_poll`, `stop_poll`, `activate_target`, `data_exchange`, `fw_download`, …)
- `struct nfc_target` — a discovered tag or peer

### 4. LLCP (`net/nfc/llcp_core.c`, `llcp_sock.c`)

Implements NFC Forum Logical Link Control Protocol. Enables socket-style
peer-to-peer communication on top of NFC-DEP (ISO 18092). Two sub-layers:
- **LLC** — link management, parameter exchange (LTO / RW / MIUX)
- **LLCP services** — SAP-based connection establishment (like TCP ports)

### 5. NCI Layer (`net/nfc/nci/`)

Implements the **NFC Controller Interface** specification (NFC Forum). Used by
modern NFC chips (NXP PN7xx, Marvell, Samsung). Handles:
- `CORE_RESET` / `CORE_INIT` — controller bring-up
- `RF_DISCOVER` / `RF_DISCOVER_SELECT` — tag polling
- `NCI_DATA_PKT` — payload exchange over logical connections
- NFCEE (Secure Element) activation via `NFCEE_DISCOVER` / `NFCEE_MODE_SET`

### 6. HCI Layer (`net/nfc/hci/`)

Implements the **Host Controller Interface** (ETSI TS 102 622). Used by older
ST Microelectronics chips (ST21NFC, Microread). Wraps HCP frames and dispatches
events through gates and pipes.

### 7. Digital Protocol Stack (`net/nfc/digital_core.c`)

Used by "bare-RF" adapters that only transmit raw frames (e.g., TI TRF7970A,
Sony port100). Implements full RF protocol state machines in software:

| Technology | Protocols |
|---|---|
| NFC-A (13.56 MHz, type A modulation) | ISO 14443-A, Mifare, Jewel, ISO-DEP, NFC-DEP |
| NFC-B (type B modulation) | ISO 14443-B, ISO-DEP |
| NFC-F (FeliCa, 212/424 kbps) | FeliCa, NFC-DEP |
| NFC-V (ISO 15693) | ISO 15693 |

### 8. Hardware Drivers (`drivers/nfc/`)

Each driver selects one integration path:

| Path | Registration | Used by |
|---|---|---|
| Via NCI | `nci_allocate_device()` | nxp-nci, nfcmrvl, st-nci, s3fwrn5 |
| Via HCI | `nfc_allocate_device()` + HCI | st21nfca, microread |
| Via Digital Stack | `nfc_digital_allocate_device()` | trf7970a, port100 |
| Direct nfc_ops | `nfc_allocate_device()` | pn533, pn544 |

---

## Tag Discovery / Data Exchange Flow

```
Userspace                NFC Core            Driver / NCI
    │                       │                    │
    │  NFC_CMD_DEV_UP        │                    │
    │ ──────────────────────►│                    │
    │                        │  nfc_dev_up()      │
    │                        │  ops->dev_up()     │
    │                        │ ──────────────────►│ power on RF
    │                        │                    │
    │  NFC_CMD_START_POLL    │                    │
    │  (protocols=NFC-A|NFC-B│                    │
    │ ──────────────────────►│                    │
    │                        │  nfc_start_poll()  │
    │                        │  ops->start_poll() │
    │                        │ ──────────────────►│ RF_DISCOVER_CMD
    │                        │                    │ ──► NFC chip
    │                        │                    │
    │                        │◄───────────────────│ RF_INTF_ACTIVATED_NTF
    │                        │  nfc_targets_found()│ (tag detected)
    │                        │                    │
    │◄──────────────────────-│ NFC_EVENT_TARGETS_FOUND
    │  (netlink event)       │                    │
    │                        │                    │
    │  connect(AF_NFC sock,  │                    │
    │    target_idx, proto)  │                    │
    │ ──────────────────────►│                    │
    │                        │  nfc_activate_target()
    │                        │  ops->activate_target()
    │                        │ ──────────────────►│ NCI_CORE_CONN_CREATE
    │                        │                    │
    │  send(sock, apdu)      │                    │
    │ ──────────────────────►│                    │
    │                        │  nfc_data_exchange()│
    │                        │  ops->im_transceive()
    │                        │ ──────────────────►│ NCI_DATA_PKT ──► tag
    │                        │◄───────────────────│ NCI_DATA_PKT ◄── tag
    │◄───────────────────────│ callback → recv     │
    │  recv(sock) → response │                    │
```

---

## Key Source Files

| File | Role |
|---|---|
| `net/nfc/core.c` | NFC device lifecycle and polling engine |
| `net/nfc/netlink.c` | Generic Netlink command/event handling |
| `net/nfc/af_nfc.c` | AF_NFC protocol family |
| `net/nfc/rawsock.c` | Raw APDU socket |
| `net/nfc/llcp_core.c` | LLCP link layer |
| `net/nfc/llcp_sock.c` | LLCP socket interface |
| `net/nfc/digital_core.c` | Software RF protocol state machine |
| `net/nfc/nci/core.c` | NCI command/response engine |
| `net/nfc/hci/core.c` | HCI gate/pipe management |
| `drivers/nfc/pn533/` | NXP PN533 (USB / SPI / I2C) |
| `drivers/nfc/nxp-nci/` | NXP NCI chips (PN7150, PN7120) |
| `drivers/nfc/trf7970a.c` | TI TRF7970A (bare-RF, SPI) |

---

## Analogy

Think of the NFC subsystem like a **post office**:

- The **hardware driver** is the mail truck — it physically delivers bytes over the RF link.
- The **NCI/HCI layer** is the postal sorting facility — it speaks a standardised protocol with the truck.
- The **NFC core** is the post office counter — it registers mailboxes (devices), starts delivery rounds (polling), and routes parcels.
- The **LLCP** is the courier service for peer-to-peer express packages.
- The **AF_NFC socket** is the PO Box that your application opens to send/receive mail.
- **Generic Netlink** is the phone line to the post office for operational commands ("open today?", "any parcels?").

---

## References

- NFC Forum NCI Specification: https://nfc-forum.org/
- `include/uapi/linux/nfc.h` — UAPI definitions
- `include/net/nfc/nfc.h` — Core driver API
- `include/net/nfc/nci.h` — NCI driver API
- `Documentation/networking/nfc.rst` (upstream kernel docs)
