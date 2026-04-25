# IEEE 802 — Generic LLC / SNAP / Token Ring Handling

## Overview

The `net/802/` subsystem implements the **IEEE 802** family of link-layer
protocol handlers inside the Linux kernel.  It sits just above the network
device driver layer and provides:

| Responsibility | What it does |
|---|---|
| **802.2 LLC SAP demux** | Registers protocol handlers keyed by LLC Service Access Point (SAP) byte and dispatches incoming `ETH_P_802_2` frames to the correct consumer. |
| **SNAP encapsulation** | Extends LLC with a 5-byte Sub-Network Access Protocol header (OUI + type) so that hundreds of upper-layer protocols can share the single SNAP SAP (`0xAA`). |
| **STP bridge frames** | Receives Spanning Tree Protocol BPDUs (destination `01:80:C2:00:00:00`) and hands them to the bridge module. |
| **Fibre Channel headers** | Builds / parses FC link-layer headers (`fc.c`). |
| **Token Ring headers** | Builds / parses IEEE 802.5 Token Ring headers and source-routing fields (`tr.c`). |

All of this code lives in `net/802/` and is compiled into the base kernel
(or as tightly-coupled modules) so that any NIC driver emitting 802.2 or
802.3 frames has a ready-made protocol-dispatch path.

---

## Data-Flow Diagram

```
  ┌─────────────────────────────────────────────────────────────────────┐
  │                     Incoming Ethernet Frame                        │
  │  [ DST | SRC | Len/Type ][ DSAP | SSAP | Ctrl | … payload … ]     │
  └────────────────────────────┬────────────────────────────────────────┘
                               │
                       ethertype / length?
                               │
               ┌───────────────┴───────────────┐
               │ Length ≤ 1500  (ETH_P_802_2)  │
               └───────────────┬───────────────┘
                               │
                         p8022_rcv()
                    look up DSAP in SAP table
                               │
              ┌────────────────┼────────────────┐
              │                │                │
         DSAP=0xAA        DSAP=0x42        other SAP
        (SNAP SAP)        (STP SAP)       (IPX, NetBEUI…)
              │                │                │
        psnap_rcv()      stp_pdu_rcv()    client->func()
              │                │
   ┌──────────┴──────┐        │
   │  Match OUI+Type │        └──▶ bridge module
   │  in SNAP table  │
   └────────┬────────┘
            │
     upper-layer protocol
     (AppleTalk, IPX-SNAP, …)
```

### Layer-by-Layer Explanation

1. **NIC driver → `netif_receive_skb()`**
   The driver marks the frame with `skb->protocol = htons(ETH_P_802_2)` when
   the Ethernet length/type field indicates an 802.3 length (≤ 1500).

2. **`p8022_rcv()` — LLC SAP dispatch** (`p8022.c`)
   Registered via `dev_add_pack()` for `ETH_P_802_2`.  Reads the DSAP byte
   from the LLC header and walks a linked list of `datalink_proto` clients
   registered with `register_8022_client()`.  If a match is found the
   client's `rcvfunc` is called; otherwise the frame is dropped.

3. **`psnap_rcv()` — SNAP sub-dispatch** (`psnap.c`)
   Itself an 802.2 client (SAP `0xAA`).  Strips the 3-byte OUI + 2-byte
   type from the SNAP header and looks up a matching entry registered
   via `register_snap_client()`.  This is how protocols like AppleTalk
   (`0x809B`) or IPX-over-SNAP ride on top of 802.2.

4. **`stp_pdu_rcv()` — Spanning Tree** (`stp.c`)
   A thin shim that receives LLC frames addressed to the well-known STP
   group MAC and forwards them to whoever called `stp_proto_register()`
   (typically the bridge module `br_stp.c`).

5. **`fc_type_trans()` — Fibre Channel** (`fc.c`)
   Sets `skb->protocol` based on the Fibre Channel header, analogous to
   `eth_type_trans()` for Ethernet.

6. **`tr_type_trans()` / `tr_rebuild_header()`** (`tr.c`)
   Token Ring equivalents of the Ethernet header helpers, including
   source-routing field parsing.

---

## Key Source Files

| File | Purpose |
|---|---|
| `net/802/p8022.c` | 802.2 LLC SAP registration (`register_8022_client` / `unregister_8022_client`) and `p8022_rcv()` receive path. |
| `net/802/psnap.c` | SNAP protocol table, `register_snap_client()` / `unregister_snap_client()`, and `snap_rcv()` demux. |
| `net/802/stp.c` | STP LLC handler — `stp_proto_register()` / `stp_proto_unregister()` plus `stp_pdu_rcv()`. |
| `net/802/fc.c` | Fibre Channel link-layer header create/parse — `fc_type_trans()`, `fc_header()`. |
| `net/802/tr.c` | Token Ring link-layer header create/parse — `tr_type_trans()`, `tr_rebuild_header()`, source-route handling. |

---

## Key Functions

| Function | File | Role |
|---|---|---|
| `register_8022_client(sap, rcvfunc)` | `p8022.c` | Register an LLC SAP handler; returns a `datalink_proto *`. |
| `unregister_8022_client(proto)` | `p8022.c` | Remove a previously registered SAP handler. |
| `p8022_rcv(skb, dev, pt, orig_dev)` | `p8022.c` | `packet_type.func` callback — dispatches 802.2 frames by DSAP. |
| `register_snap_client(oui_type, rcvfunc)` | `psnap.c` | Register a SNAP protocol handler (OUI + ethertype). |
| `unregister_snap_client(proto)` | `psnap.c` | Remove a SNAP handler. |
| `snap_rcv(skb, dev, pt, orig_dev)` | `psnap.c` | Receive path for SAP 0xAA — demuxes by OUI+type. |
| `stp_proto_register(proto)` | `stp.c` | Register a consumer for STP BPDUs. |
| `stp_proto_unregister(proto)` | `stp.c` | Unregister the STP consumer. |
| `fc_type_trans(skb, dev)` | `fc.c` | Determine `skb->protocol` from an FC header. |
| `tr_type_trans(skb, dev)` | `tr.c` | Determine `skb->protocol` from a Token Ring header. |

---

## Analogy

Think of the 802.2 LLC layer as a **building mail-room**.  Every letter
(frame) arrives at the front desk.  The DSAP byte is the **room number** —
the mail clerk looks it up on the directory board (`register_8022_client`
table) and delivers the letter to the right tenant.

SNAP is a **sub-let extension**: room 0xAA is actually a shared co-working
space.  Inside, each desk has a unique name tag (OUI + type), and the
co-working receptionist (`psnap_rcv`) routes the letter to the correct
desk.

STP is the **fire-alarm intercom** — a special broadcast that goes straight
to the building manager (bridge module) so it can decide which doors
(ports) to keep open or close.

---

## References

- IEEE 802.2 (LLC) — ISO/IEC 8802-2
- IEEE 802.1D — Spanning Tree Protocol
- RFC 1042 — *A Standard for the Transmission of IP Datagrams over IEEE 802 Networks* (SNAP encapsulation)
- `net/802/` in the Linux kernel source tree (`include/net/p8022.h`, `include/net/psnap.h`)
- `Documentation/networking/` in the kernel tree
