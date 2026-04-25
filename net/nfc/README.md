# NFC Subsystem — Near Field Communication

## Overview

The Linux **NFC (Near Field Communication)** subsystem provides a unified
framework for short-range (≤ 10 cm) wireless communication at 13.56 MHz.  It
supports the major NFC standards — NCI, HCI, LLCP, NFC-DEP — and exposes
devices to user-space through both an `AF_NFC` socket family and a generic
netlink interface.

Key capabilities:

| Feature | Purpose |
|---------|---------|
| **NFC Core** | Device model, polling, target discovery, generic-netlink control |
| **NCI (NFC Controller Interface)** | Standardised host ↔ controller protocol (NCI 1.0 / 2.0) |
| **HCI (Host Controller Interface)** | Pipe/gate-based transport for older or SWP controllers |
| **LLCP (Logical Link Control Protocol)** | Connection-oriented & connectionless peer-to-peer links |
| **Digital Protocol Layer** | Software implementation of NFC-A/-B/-F/-V framing |
| **NFC-DEP (Data Exchange Protocol)** | Peer-to-peer data transport over the RF interface |

---

## Architecture

```
 ┌─────────────────────────────────────────────────────────────────┐
 │                        User-space                              │
 │   nfctool / neard / libnfc / custom app                        │
 └──────────┬──────────────────────┬──────────────────────────────┘
            │ AF_NFC sockets       │ Generic Netlink (nl80211-style)
 ═══════════╪══════════════════════╪══════════════════════════════════  kernel
 ┌──────────▼──────────────────────▼──────────────────────────────┐
 │                       NFC Core  (net/nfc/core.c)               │
 │  struct nfc_dev · polling · target list · genl command dispatch │
 └──────┬───────────────┬────────────────┬───────────────┬────────┘
        │               │                │               │
 ┌──────▼──────┐ ┌──────▼──────┐ ┌───────▼──────┐ ┌─────▼────────┐
 │  NCI layer  │ │  HCI layer  │ │  LLCP layer  │ │ Digital layer│
 │ net/nfc/nci │ │ net/nfc/hci │ │ net/nfc/llcp │ │net/nfc/digital│
 │             │ │             │ │              │ │              │
 │ NCI cmds &  │ │ pipes/gates │ │ connection-  │ │ NFC-A/B/F/V  │
 │ data xchg   │ │ & registry  │ │ less + conn- │ │ framing in   │
 │             │ │             │ │ oriented P2P │ │ software     │
 └──────┬──────┘ └──────┬──────┘ └──────────────┘ └──────┬───────┘
        │               │                                │
 ┌──────▼───────────────▼────────────────────────────────▼────────┐
 │           Platform / Transport drivers                         │
 │   (SPI, I²C, UART, USB)  →  NFC Controller hardware           │
 └────────────────────────────────────────────────────────────────┘
```

### Data-path walkthrough (tag read)

```
 App  ──socket(AF_NFC)──►  NFC core
                              │
                         nfc_start_poll()
                              │
                       target discovered
                              │
                       nfc_activate_target()
                              │
                         nci_send_cmd()  ─────►  NFC controller
                              │                       │
                         nci_recv_frame() ◄───────────┘
                              │
                       skb delivered to App via recvmsg()
```

---

## Layer-by-layer Explanation

### NFC Core (`net/nfc/core.c`)

The core layer owns `struct nfc_dev`, the central object representing one NFC
controller.  It provides:

* **Device registration** — `nfc_register_device()` / `nfc_unregister_device()`
* **Polling state-machine** — `nfc_start_poll()` / `nfc_stop_poll()`
* **Target management** — discovered targets stored in a per-device list
* **Generic Netlink API** — `nfc_genl_dev_up` / `nfc_genl_dev_down` and
  friends let user-space enumerate, activate, and configure controllers
* **SE (Secure Element) hooks** — for SIM / embedded-SE access

### NCI Transport (`net/nfc/nci/`)

NCI implements the *NFC Controller Interface* specification.  The host sends
**commands**, the controller replies with **responses**, and asynchronous
**notifications** flow from controller to host.

```
 Host                          NFCC
  │── NCI_OP_CORE_INIT ──────►│
  │◄── NCI_RSP_CORE_INIT ─────│
  │                            │
  │── NCI_OP_RF_DISCOVER ────►│
  │◄── NCI_NTF_RF_DISCOVER ───│   (target found)
```

Key API: `nci_register_device()`, `nci_send_cmd()`, `nci_recv_frame()`.

### HCI Transport (`net/nfc/hci/`)

HCI uses a **pipe/gate** model.  Logical connections (pipes) are opened to
named functional units (gates) inside the controller.  The Linux
implementation layers HCI on top of NCI when the hardware supports both.

Key structure: `struct nfc_hci_dev`.

### LLCP — Logical Link Control Protocol (`net/nfc/llcp/`)

LLCP enables **peer-to-peer** communication between two NFC devices.  It
offers two service classes:

| Mode | Socket type | Reliability |
|------|-------------|-------------|
| Connectionless | `SOCK_DGRAM` | Best-effort, UI frames |
| Connection-oriented | `SOCK_STREAM` | Sequenced, flow-controlled |

Sockets are represented by `struct nfc_llcp_sock` and use service-name
resolution (SNL) to discover remote services by well-known name.

Key function: `nfc_llcp_send_ui_frame()`.

### Digital Protocol Layer (`net/nfc/digital/`)

A pure-software implementation of the NFC analog/digital framing for
controllers that leave framing to the host.  Covers:

* NFC-A (ISO 14443-3A)
* NFC-B (ISO 14443-3B)
* NFC-F (JIS X 6319-4 / FeliCa)
* NFC-V (ISO 15693 / Vicinity)

---

## Key Structures

| Structure | Header | Role |
|-----------|--------|------|
| `struct nfc_dev` | `include/net/nfc/nfc.h` | One NFC controller: ops, targets, polling state |
| `struct nci_dev` | `include/net/nfc/nci_core.h` | NCI-specific state: cmd queue, timers, data pipes |
| `struct nfc_llcp_sock` | `net/nfc/llcp.h` | LLCP socket: SAP addresses, Tx/Rx queues |
| `struct nfc_hci_dev` | `include/net/nfc/hci.h` | HCI device: pipe table, gate registry |
| `struct nfc_digital_dev` | `include/net/nfc/digital.h` | Digital-layer framing state |
| `struct nfc_target` | `include/net/nfc/nfc.h` | Discovered tag/peer: protocol, NFCID, sens_res |

---

## Key Functions

| Function | File | Purpose |
|----------|------|---------|
| `nfc_register_device()` | `net/nfc/core.c` | Register controller, create `/sys/class/nfc/nfcN` |
| `nfc_unregister_device()` | `net/nfc/core.c` | Tear down device and release targets |
| `nfc_alloc_recv_skb()` | `net/nfc/core.c` | Allocate an `sk_buff` for incoming NFC data |
| `nfc_start_poll()` | `net/nfc/core.c` | Begin RF field polling for targets |
| `nfc_stop_poll()` | `net/nfc/core.c` | Stop polling |
| `nfc_genl_dev_up()` | `net/nfc/netlink.c` | Netlink: power-on a controller |
| `nfc_genl_dev_down()` | `net/nfc/netlink.c` | Netlink: power-off a controller |
| `nci_register_device()` | `net/nfc/nci/core.c` | Register an NCI controller |
| `nci_send_cmd()` | `net/nfc/nci/core.c` | Queue an NCI command to the NFCC |
| `nci_recv_frame()` | `net/nfc/nci/core.c` | Process an incoming NCI frame |
| `nfc_llcp_send_ui_frame()` | `net/nfc/llcp_commands.c` | Transmit a connectionless LLCP UI frame |
| `nfc_hci_send_cmd()` | `net/nfc/hci/core.c` | Send an HCI command to a gate/pipe |

---

## Common Operations

### Register an NFC controller (driver init)

```c
struct nfc_dev *ndev;

ndev = nfc_allocate_device(&my_ops, supported_protocols,
                           tx_headroom, tx_tailroom);
nfc_register_device(ndev);
```

### Start polling for tags

```c
/* User-space via generic netlink, or in-kernel: */
nfc_start_poll(ndev, NFC_PROTO_ISO14443_MASK | NFC_PROTO_MIFARE_MASK);
```

### Send an NCI command

```c
struct nci_dev *nci;

nci_send_cmd(nci, NCI_OP_CORE_INIT, 0, NULL);
/* Response arrives asynchronously via nci->ops->recv() */
```

### LLCP peer-to-peer send

```c
struct sk_buff *skb = nfc_alloc_recv_skb(GFP_KERNEL, payload_len);
/* populate skb->data */
nfc_llcp_send_ui_frame(llcp_sock, dsap, ssap, skb);
```

---

## Key Source Files

| Path | Description |
|------|-------------|
| `net/nfc/core.c` | NFC device model, polling, target management |
| `net/nfc/netlink.c` | Generic-netlink command handlers |
| `net/nfc/af_nfc.c` | `AF_NFC` socket family registration |
| `net/nfc/rawsock.c` | Raw NFC sockets for direct tag access |
| `net/nfc/llcp_core.c` | LLCP link management and socket layer |
| `net/nfc/llcp_commands.c` | LLCP PDU construction and transmission |
| `net/nfc/llcp_sock.c` | LLCP socket operations (bind, connect, sendmsg) |
| `net/nfc/nci/core.c` | NCI state-machine, cmd/rsp/ntf dispatch |
| `net/nfc/nci/data.c` | NCI data exchange (send/receive credits) |
| `net/nfc/nci/ntf.c` | NCI notification handlers |
| `net/nfc/nci/rsp.c` | NCI response handlers |
| `net/nfc/nci/hci.c` | NCI-HCI bridge (HCI over NCI) |
| `net/nfc/hci/core.c` | HCI device, pipe, and gate management |
| `net/nfc/hci/command.c` | HCI command building and sending |
| `net/nfc/hci/llc.c` | HCI logical-link-control framing |
| `net/nfc/digital/digital_core.c` | Digital-layer core and polling loops |
| `net/nfc/digital/digital_technology.c` | NFC-A/B/F/V technology framing |
| `net/nfc/digital/digital_dep.c` | NFC-DEP (Data Exchange Protocol) |
| `include/net/nfc/nfc.h` | Core data-structures and API |
| `include/net/nfc/nci.h` | NCI opcodes and constants |
| `include/net/nfc/nci_core.h` | NCI internal structures |
| `include/net/nfc/hci.h` | HCI data-structures and API |
| `include/net/nfc/digital.h` | Digital-layer API |
| `include/uapi/linux/nfc.h` | User-space UAPI: protocols, commands, events |

---

## Analogy

Think of the NFC subsystem as a **hotel concierge desk**:

* **NFC Core** is the concierge — it knows every room (controller), greets
  arriving guests (targets), and routes requests.
* **NCI** is the internal phone system — a standardised way the concierge
  talks to hotel services (the controller) using request/response calls.
* **HCI** is an older intercom with numbered pipes — same job, different
  wiring, sometimes layered on top of the phone system.
* **LLCP** is the guest-to-guest chat service — two visitors can exchange
  messages directly (peer-to-peer), either quick notes (connectionless) or a
  full conversation (connection-oriented).
* **Digital layer** is the concierge manually reading a guest's foreign ID
  card — when the scanner (controller) cannot decode it on its own, the
  concierge does the framing in software.

---

## References

* `Documentation/networking/nfc.rst` — upstream kernel NFC documentation
* NFC Forum specifications: NCI 1.0 / 2.0, LLCP 1.3, Digital Protocol 2.2
* ETSI TS 102 622 — HCI specification
* ISO/IEC 18092 — NFC-DEP (NFCIP-1)
* ISO/IEC 14443 — NFC-A / NFC-B proximity cards
* JIS X 6319-4 — NFC-F (FeliCa)
* `neard` project — reference NFC daemon for Linux
