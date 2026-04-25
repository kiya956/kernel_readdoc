# IEEE 802.15.4 — Low-Rate Wireless Personal Area Networks

## Overview

The IEEE 802.15.4 subsystem implements the network-layer support for low-rate
wireless personal area networks (LR-WPANs) in the Linux kernel. It provides
the foundation for 6LoWPAN, ZigBee, and Thread networking stacks. The subsystem
manages:

- **AF_IEEE802154 sockets** for raw 802.15.4 frame access
- **WPAN device registration** and configuration
- **Frame reception and delivery** to upper-layer protocols
- **PAN management** — addressing, association, security

IEEE 802.15.4 devices operate on low-power, low-data-rate radio channels
(250 kbps at 2.4 GHz) with short-range communication (10–100 meters).

## Kernel Source

- **Directory:** `net/ieee802154/`
- **Headers:** `include/net/ieee802154_netdev.h`, `include/net/cfg802154.h`
- **Config:** `CONFIG_IEEE802154`, `CONFIG_IEEE802154_SOCKET`

## Architecture

```
┌─────────────────────────────────────────────┐
│         User Space (wpan-tools)             │
│     AF_IEEE802154 sockets / nl802154        │
├─────────────────────────────────────────────┤
│        IEEE 802.15.4 Socket Layer           │
│     RAW sockets / DGRAM sockets            │
├─────────────────────────────────────────────┤
│        6LoWPAN Adaptation Layer             │
│    Header compression, fragmentation        │
├─────────────────────────────────────────────┤
│      IEEE 802.15.4 Network Layer            │
│  ieee802154_rcv, frame parsing, delivery    │
├─────────────────────────────────────────────┤
│         cfg802154 / nl802154                │
│   WPAN device config via netlink            │
├─────────────────────────────────────────────┤
│       mac802154 (Software MAC)              │
│   CSMA/CA, scanning, beaconing             │
├─────────────────────────────────────────────┤
│      WPAN PHY Driver (Hardware)             │
│    at86rf230, cc2520, adf7242, etc.         │
└─────────────────────────────────────────────┘
```

## Packet Flow

```
 SEND PATH                              RECEIVE PATH
 ─────────                              ────────────

 AF_IEEE802154 socket                   WPAN PHY interrupt
     │                                       │
     ▼                                       ▼
 ieee802154_sock_sendmsg()             ieee802154_rcv()
     │                                       │
     ▼                                       ▼
 Build 802.15.4 header                 Parse MHR (MAC Header)
 (FCF, seq, addressing)               Validate FCS
     │                                       │
     ▼                                       ▼
 dev_queue_xmit()                      ieee802154_deliver_skb()
     │                                       │
     ▼                                  ┌────┴─────┐
 mac802154 TX path                     │          │
     │                                  ▼          ▼
     ▼                              6LoWPAN    Raw socket
 WPAN PHY transmit                  handler    delivery
```

## Key Structures

| Structure | File | Purpose |
|-----------|------|---------|
| `struct ieee802154_local` | `include/net/cfg802154.h` | Per-device state for 802.15.4 PHY |
| `struct wpan_dev` | `include/net/cfg802154.h` | Wireless PAN device — like wireless_dev for WiFi |
| `struct wpan_phy` | `include/net/cfg802154.h` | PHY capabilities and configuration |
| `struct ieee802154_addr` | `include/net/ieee802154_netdev.h` | 802.15.4 address (short or extended) |

## Key Functions

| Function | File | Purpose |
|----------|------|---------|
| `ieee802154_rcv()` | `net/ieee802154/core.c` | Main receive entry for 802.15.4 frames |
| `ieee802154_deliver_skb()` | `net/ieee802154/rx.c` | Deliver frame to matching sockets |
| `cfg802154_register_device()` | `net/ieee802154/core.c` | Register a WPAN PHY device |
| `nl802154_send_msg()` | `net/ieee802154/nl802154.c` | Send netlink config messages |
| `ieee802154_hdr_push()` | `net/ieee802154/header_ops.c` | Push 802.15.4 MAC header onto skb |
| `ieee802154_hdr_pull()` | `net/ieee802154/header_ops.c` | Pull and parse 802.15.4 MAC header |

## Analogy

Think of IEEE 802.15.4 as a **walkie-talkie network for tiny sensors**. Each sensor
has a short ID (like a channel number) and belongs to a PAN (like a radio group).
The `net/ieee802154/` layer is the dispatcher that receives incoming radio messages
and routes them to the right listener. `mac802154` handles the radio etiquette
(waiting for clear channel, retransmitting), while this layer handles addressing
and socket delivery — like a mail room that sorts packages by room number.
