# Linux Kernel Bluetooth Protocol Stack

## Overview

The **Linux Bluetooth subsystem** implements the full Bluetooth protocol stack
in-kernel, from the low-level **HCI** (Host Controller Interface) transport up
through **L2CAP** (Logical Link Control and Adaptation Protocol), **SCO**
(Synchronous Connection-Oriented links for audio), **RFCOMM** (serial port
emulation), **BNEP** (Bluetooth Network Encapsulation Protocol for PAN),
**HIDP** (Human Interface Device Protocol), and **SMP** (Security Manager
Protocol for LE pairing).

The subsystem supports both **Bluetooth Classic (BR/EDR)** and **Bluetooth Low
Energy (LE)** transports, with userspace management provided by **BlueZ**
(`bluetoothd`, `bluetoothctl`, `hcitool`, `btmgmt`).

Source: `net/bluetooth/`, `include/net/bluetooth/`, `drivers/bluetooth/`.

---

## Subsystem Stack

```
┌─────────────────────────────────────────────────────────────────┐
│                        USERSPACE                                │
│                                                                 │
│   BlueZ daemon (bluetoothd)      bluetoothctl / btmgmt          │
│     ├── GATT client/server       hcitool / hcidump              │
│     ├── SDP service discovery    PulseAudio/PipeWire (A2DP/SCO) │
│     └── Agent (pairing UI)       Application profiles           │
└──────────────────────────────┬──────────────────────────────────┘
                               │  AF_BLUETOOTH sockets
              ┌────────────────┴─────────────────────┐
              │                                      │
     Management (mgmt.c)              Profile sockets
     btmgmt / D-Bus API              (RFCOMM, BNEP, HIDP, SCO)
              │                                      │
┌─────────────▼──────────────────────────────────────▼───────────┐
│                     L2CAP LAYER (l2cap_core.c)                  │
│                                                                 │
│  Multiplexes logical channels over ACL links                    │
│  Connection-oriented (streams) and connectionless (datagrams)   │
│  LE Credit-Based Flow Control (CoC)                             │
│  Segmentation and Reassembly (SAR)                              │
│  Channel modes: Basic, ERTM, Streaming, LE CoC                  │
│                                                                 │
│  Fixed channels: SMP (CID 0x0006), ATT (CID 0x0004),           │
│                  Signaling (CID 0x0001/0x0005)                  │
└──────────────────────────────┬──────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────┐
│                     HCI CORE (hci_core.c, hci_event.c)          │
│                                                                 │
│  struct hci_dev — per-controller state machine                  │
│  struct hci_conn — per-connection (ACL, SCO, LE, ISO)           │
│  Command/event processing, connection management                │
│  SMP (smp.c) — LE pairing, key distribution, CTKD              │
│  Advertising, scanning, resolving list management               │
└──────────────────────────────┬──────────────────────────────────┘
                               │ hci_send_frame / hci_recv_frame
┌──────────────────────────────▼──────────────────────────────────┐
│                     HCI DRIVER (drivers/bluetooth/)             │
│                                                                 │
│  btusb.c — USB Bluetooth adapters (most common)                 │
│  hci_uart.c — UART/serial transports (H4, H5, BCSP, LL, QCA)   │
│  btsdio.c — SDIO Bluetooth (combo WiFi/BT chips)               │
│  virtio_bt.c — Virtualized Bluetooth (QEMU/KVM)                │
└──────────────────────────────┬──────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────┐
│                   BLUETOOTH CONTROLLER HARDWARE                  │
│   Baseband  ──  Link Manager  ──  Radio (2.4 GHz ISM)          │
└─────────────────────────────────────────────────────────────────┘
```

---

## Layer-by-Layer Explanation

### 1. HCI Core (`hci_core.c`, `hci_event.c`, `hci_request.c`)

The HCI layer manages Bluetooth controllers as `struct hci_dev` instances.
Each controller transitions through states: DOWN → INIT → RUNNING.

- **Command processing**: HCI commands are queued and sent to the controller;
  responses arrive as HCI events processed by `hci_event_packet()`.
- **Connection management**: `struct hci_conn` tracks each ACL, SCO, LE, or
  ISO link. Connection setup, authentication, and teardown are state-machine
  driven.
- **`hci_send_acl()`**: Fragments and queues ACL data for transmission.
- **`hci_recv_frame()`**: Entry point for all data received from the controller.

### 2. L2CAP (`l2cap_core.c`, `l2cap_sock.c`)

L2CAP provides multiplexed logical channels over ACL links:

- **Connection-Oriented Channels**: PSM-based, used by RFCOMM, BNEP, AVDTP.
  Supports ERTM (Enhanced Retransmission Mode) for reliability.
- **Connectionless**: Used for broadcast data.
- **LE Credit-Based Flow Control (CoC)**: Efficient data transfer for BLE
  applications with backpressure via credits.
- **Fixed Channels**: ATT (CID 0x0004), SMP (CID 0x0006), L2CAP signaling.
- **`l2cap_recv_frame()`**: Dispatches incoming ACL data to the correct channel.

### 3. SMP — Security Manager Protocol (`smp.c`)

Handles Bluetooth LE pairing and key distribution:

- **Pairing methods**: Just Works, Passkey Entry, Numeric Comparison, OOB
- **Key types**: LTK (Long-Term Key), IRK (Identity Resolving Key), CSRK
- **Cross-Transport Key Derivation (CTKD)**: Derives BR/EDR keys from LE and
  vice versa.
- **`smp_distribute_keys()`**: Sends generated keys to the peer after pairing.

### 4. SCO / eSCO (`sco.c`)

Synchronous Connection-Oriented links for voice/audio:

- **SCO**: 64 kbps, fixed bandwidth, used for HFP telephony audio
- **eSCO**: Extended SCO with retransmissions, configurable packet types
- **`sco_connect()`**: Establishes a synchronous audio link

### 5. RFCOMM (`rfcomm/`)

Serial port emulation over L2CAP (PSM 3):

- Multiplexes up to 60 virtual serial channels over one L2CAP connection
- Used by SPP (Serial Port Profile), DUN, and legacy profiles

### 6. Management Interface (`mgmt.c`)

The kernel management API used by BlueZ's `bluetoothd`:

- Controls adapter power, discoverable mode, pairing, connections
- Exposes events for userspace: new connections, pairing requests, etc.
- **`mgmt_control()`**: Entry point for management commands from userspace

---

## Key Data Structures

| Structure | Purpose |
|---|---|
| `struct hci_dev` | Per-controller state: flags, queues, connections, keys |
| `struct hci_conn` | Per-connection: type (ACL/SCO/LE/ISO), state, keys, link quality |
| `struct l2cap_chan` | Per-L2CAP channel: PSM, CID, mode, MTU, credits, tx/rx queues |
| `struct bt_sock` | Bluetooth socket: family AF_BLUETOOTH, per-protocol state |
| `struct smp_chan` | SMP pairing context: method, keys, ECDH state |
| `struct rfcomm_dlc` | RFCOMM data link connection (virtual serial port) |
| `struct mgmt_pending_cmd` | Pending management command awaiting HCI response |

## Key Functions

| Function | Purpose |
|---|---|
| `hci_send_acl()` | Fragment and queue ACL data for transmission |
| `hci_send_frame()` | Send raw HCI frame to the controller driver |
| `hci_recv_frame()` | Receive raw HCI frame from controller (driver callback) |
| `hci_event_packet()` | Process incoming HCI event from controller |
| `l2cap_recv_frame()` | Dispatch received ACL data to L2CAP channels |
| `l2cap_recv_acldata()` | Reassemble and deliver ACL fragments to L2CAP |
| `l2cap_connect()` | Initiate L2CAP channel connection |
| `sco_connect()` | Establish SCO/eSCO synchronous audio link |
| `smp_distribute_keys()` | Send pairing keys to remote peer |
| `bt_sock_create()` | Create AF_BLUETOOTH socket |
| `mgmt_control()` | Process management command from userspace |
| `hci_register_dev()` | Register a new HCI controller with the stack |

## Key Source Files

| File | Purpose |
|---|---|
| `net/bluetooth/hci_core.c` | HCI controller management and state machine |
| `net/bluetooth/hci_event.c` | HCI event processing |
| `net/bluetooth/hci_conn.c` | HCI connection lifecycle |
| `net/bluetooth/l2cap_core.c` | L2CAP protocol engine |
| `net/bluetooth/l2cap_sock.c` | L2CAP socket interface |
| `net/bluetooth/smp.c` | Security Manager Protocol (LE pairing) |
| `net/bluetooth/sco.c` | SCO audio links |
| `net/bluetooth/rfcomm/core.c` | RFCOMM serial emulation |
| `net/bluetooth/mgmt.c` | Management interface for BlueZ |
| `net/bluetooth/af_bluetooth.c` | AF_BLUETOOTH socket family |
| `drivers/bluetooth/btusb.c` | USB HCI transport driver |
| `include/net/bluetooth/hci_core.h` | Core HCI structures |
| `include/net/bluetooth/l2cap.h` | L2CAP structures and constants |

---

## Analogy

The Bluetooth stack is like a **multi-floor office building's mail system**:

- The **HCI layer** is the loading dock — all packages (data) enter and leave
  through a single physical point (the radio), managed by dock workers (the
  controller firmware).
- **L2CAP** is the internal mail room — it sorts packages by department
  (channel/PSM), handles fragmentation (splitting large parcels), and manages
  flow control (don't flood a department with more than they can handle).
- **RFCOMM, SCO, BNEP** are individual departments — each has its own rules
  for handling mail (serial streams, real-time audio, IP packets).
- **SMP** is building security — it verifies identities (pairing), issues
  keycards (encryption keys), and controls who can access what.
- **BlueZ** is the receptionist at the front desk — it coordinates everything
  for visitors (applications) and makes the complex internal routing invisible.

---

## References

- `include/net/bluetooth/hci_core.h` — HCI core structures
- `include/net/bluetooth/l2cap.h` — L2CAP protocol definitions
- `include/net/bluetooth/bluetooth.h` — AF_BLUETOOTH family
- `Documentation/networking/bluetooth.rst`
- `net/bluetooth/` — Full implementation
- Bluetooth Core Specification v5.4 (bluetooth.com)
