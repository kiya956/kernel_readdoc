# LLC — Logical Link Control (IEEE 802.2)

## Overview

The LLC subsystem implements the IEEE 802.2 Logical Link Control protocol in the
Linux kernel. LLC provides a uniform interface to upper-layer protocols over
various IEEE 802 MAC sublayers (Ethernet, Token Ring, etc.). It supports two
operational modes:

- **Type 1 (Connectionless):** Unacknowledged datagram service
- **Type 2 (Connection-Oriented):** Reliable, sequenced delivery with flow control

LLC uses **Service Access Points (SAPs)** as addressing endpoints, identified by
8-bit SAP values in the LLC header. Common SAPs include 0xAA (SNAP), 0x06 (IP),
and 0xE0 (NetBIOS).

## Kernel Source

- **Directory:** `net/llc/`
- **Headers:** `include/net/llc.h`, `include/net/llc_pdu.h`, `include/net/llc_sap.h`
- **Config:** `CONFIG_LLC`, `CONFIG_LLC2`

## Architecture

```
┌─────────────────────────────────────────────┐
│          Upper-Layer Protocols              │
│       (IPX, NetBIOS, SNA, SNAP)            │
├─────────────────────────────────────────────┤
│              LLC Interface                  │
│         llc_sap_open / llc_sap_close        │
├──────────────────┬──────────────────────────┤
│   LLC Type 1     │      LLC Type 2          │
│  (Connectionless)│  (Connection-Oriented)   │
│   UI frames      │  I/S/U frames, flow ctrl │
├──────────────────┴──────────────────────────┤
│            LLC Core Engine                  │
│   SAP management, PDU build/parse, FSM     │
├─────────────────────────────────────────────┤
│           MAC Sublayer (802.3/802.5)        │
│         dev_queue_xmit / netif_rx          │
├─────────────────────────────────────────────┤
│         Network Interface (NIC)             │
└─────────────────────────────────────────────┘
```

## Packet Flow

```
 SEND PATH                              RECEIVE PATH
 ─────────                              ────────────

 Upper Layer                            NIC receives frame
     │                                       │
     ▼                                       ▼
 llc_build_and_send_pkt()              llc_rcv()
     │                                       │
     ▼                                       ▼
 llc_pdu_header_init()                 Parse LLC header
     │                                  (DSAP/SSAP/Control)
     ▼                                       │
 Set DSAP, SSAP, Control               ┌─────┴──────┐
     │                                  │            │
     ▼                                  ▼            ▼
 llc_conn_send_pdu()              SAP lookup    Type 2 FSM
  or llc_send_disc()              llc_sap_find  llc_conn_handler
     │                                  │            │
     ▼                                  ▼            ▼
 dev_queue_xmit()                 Deliver to    Update state,
     │                            upper layer   send ack/rej
     ▼                                  │            │
   NIC TX                               ▼            ▼
                                  Socket recv   Socket recv
```

## Key Structures

| Structure | File | Purpose |
|-----------|------|---------|
| `struct llc_sap` | `include/net/llc.h` | Service Access Point — binds a SAP value to handler |
| `struct llc_sock` | `include/net/llc_conn.h` | Per-connection state for LLC Type 2 sockets |
| `struct llc_pdu_sn` | `include/net/llc_pdu.h` | LLC PDU header with sequence numbers |
| `struct llc_pdu_un` | `include/net/llc_pdu.h` | LLC PDU header for unnumbered frames |

## Key Functions

| Function | File | Purpose |
|----------|------|---------|
| `llc_rcv()` | `net/llc/llc_input.c` | Main receive entry — dispatches to SAP/connection |
| `llc_build_and_send_pkt()` | `net/llc/llc_output.c` | Build LLC PDU and transmit via MAC |
| `llc_sap_open()` | `net/llc/llc_core.c` | Register a SAP for receiving frames |
| `llc_sap_close()` | `net/llc/llc_core.c` | Unregister a SAP |
| `llc_conn_state_process()` | `net/llc/llc_conn.c` | Type 2 connection state machine |
| `llc_pdu_header_init()` | `net/llc/llc_output.c` | Initialize PDU header fields |

## Analogy

Think of LLC like a **postal sorting office** inside a building. The building's
front door is the MAC layer (Ethernet). Inside, each office has a mailbox number
(SAP). Type 1 is like dropping a letter in a mailbox — no confirmation. Type 2 is
like registered mail — you get a receipt, and lost letters are re-sent. The sorting
office doesn't care what's in the letters; it just routes them to the right mailbox.
