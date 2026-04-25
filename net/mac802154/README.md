# mac802154 — Software MAC Layer for IEEE 802.15.4

## Overview

The mac802154 subsystem implements the software MAC (Medium Access Control) layer
for IEEE 802.15.4 wireless personal area networks. It is the WPAN equivalent of
mac80211 for WiFi — providing a standardized interface between the hardware PHY
drivers and the network stack.

mac802154 handles:

- **CSMA/CA** — Carrier Sense Multiple Access with Collision Avoidance
- **Frame transmission** — queueing, retry, and backoff
- **Frame reception** — filtering, validation, and delivery
- **Scanning** — active/passive/energy detection
- **Security** — hardware-assisted AES-CCM encryption

## Kernel Source

- **Directory:** `net/mac802154/`
- **Headers:** `include/net/mac802154.h`
- **Config:** `CONFIG_MAC802154`

## Architecture

```
┌─────────────────────────────────────────────┐
│      Upper Layers (6LoWPAN / Sockets)       │
├─────────────────────────────────────────────┤
│         net/ieee802154 (Network)            │
├─────────────────────────────────────────────┤
│           mac802154 Core                    │
│  ┌───────────┬───────────┬───────────┐      │
│  │  TX Path  │  RX Path  │  Scan     │      │
│  │  CSMA/CA  │  Filter   │  Engine   │      │
│  │  Queue    │  Validate │  ED/Active│      │
│  └─────┬─────┴─────┬─────┴─────┬─────┘      │
│        │           │           │             │
│  ┌─────┴───────────┴───────────┴─────┐      │
│  │    ieee802154_sub_if_data         │      │
│  │    (Virtual Interface)            │      │
│  └───────────────┬───────────────────┘      │
├──────────────────┼──────────────────────────┤
│    ieee802154_hw / ieee802154_ops           │
│    (Driver Interface)                       │
├──────────────────┼──────────────────────────┤
│         WPAN PHY Driver                     │
│    at86rf230, cc2520, mcr20a, etc.          │
└──────────────────┴──────────────────────────┘
```

## Packet Flow

```
 TX PATH                                RX PATH
 ───────                                ───────

 Upper layer xmit                       PHY IRQ / RX complete
     │                                       │
     ▼                                       ▼
 mac802154_subif_start_xmit()          ieee802154_rx_irqsafe()
     │                                       │
     ▼                                       ▼
 ieee802154_tx()                       ieee802154_rx()
     │                                       │
     ▼                                       ▼
 ieee802154_xmit_worker()             mac802154_parse_frame()
     │                                       │
     ▼                                       ▼
 CSMA/CA backoff                       Address filtering
 ieee802154_csma_ca_work()             mac802154_frame_filter()
     │                                       │
     ▼                                       ▼
 drv_xmit_async()                      ieee802154_deliver_skb()
     │                                       │
     ▼                                       ▼
 PHY driver ->xmit()                   Upper layer recv
```

## Key Structures

| Structure | File | Purpose |
|-----------|------|---------|
| `struct ieee802154_local` | `net/mac802154/ieee802154_i.h` | Per-PHY device private state |
| `struct ieee802154_sub_if_data` | `net/mac802154/ieee802154_i.h` | Per virtual-interface state |
| `struct ieee802154_hw` | `include/net/mac802154.h` | Hardware abstraction for PHY drivers |
| `struct ieee802154_ops` | `include/net/mac802154.h` | Driver callback operations |

## Key Functions

| Function | File | Purpose |
|----------|------|---------|
| `mac802154_subif_start_xmit()` | `net/mac802154/tx.c` | TX entry from net_device_ops |
| `ieee802154_rx()` | `net/mac802154/rx.c` | Main RX processing path |
| `ieee802154_rx_irqsafe()` | `net/mac802154/rx.c` | IRQ-safe RX handoff from driver |
| `ieee802154_xmit_worker()` | `net/mac802154/tx.c` | Workqueue-based frame transmission |
| `ieee802154_register_hw()` | `net/mac802154/main.c` | Register hardware with mac802154 |
| `ieee802154_alloc_hw()` | `net/mac802154/main.c` | Allocate ieee802154_hw structure |

## Analogy

mac802154 is like a **traffic controller at a small-town intersection**. The PHY
driver is the road itself. When a car (frame) wants to cross, mac802154 checks if
the road is clear (CSMA/CA), waits if needed, and sends the car through. On the
receiving side, it checks the license plate (address filtering) and directs the
car to the right parking lot (socket). Unlike a big-city highway (mac80211/WiFi),
this intersection handles lightweight, infrequent traffic — perfect for IoT sensors.
