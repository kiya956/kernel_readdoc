# 6LoWPAN — IPv6 over Low-Power Wireless Personal Area Networks

## Overview

**6LoWPAN** (IPv6 over Low-Power Wireless Personal Area Networks, RFC 4944/6282)
is a Linux kernel subsystem that enables **IPv6 networking over constrained
radio links** such as IEEE 802.15.4 (Zigbee/Thread) and Bluetooth Low Energy
(BLE).  Because these links have tiny MTUs (127 bytes for 802.15.4, 23–251 bytes
for BLE), 6LoWPAN provides:

- **IPHC (IP Header Compression)** — compresses IPv6 headers from 40 bytes to
  as few as 2–3 bytes by eliding fields derivable from link-layer context
- **Fragmentation/Reassembly** — splits IPv6 packets larger than the link MTU
  into multiple link-layer frames
- **NHC (Next Header Compression)** — compresses UDP, TCP, ICMPv6, and extension
  headers
- **Neighbor Discovery integration** — simplified ND for mesh links

Source: `net/6lowpan/`, `drivers/net/ieee802154/`, `net/ieee802154/`,
`include/net/6lowpan.h`.

---

## Subsystem Stack

```
┌────────────────────────────────────────────────────────────────┐
│                        USERSPACE                               │
│  ping6 :: socket(AF_INET6, SOCK_DGRAM)                        │
│  CoAP / Thread / Zigbee application                           │
└──────────────────────────────┬─────────────────────────────────┘
                               │  IPv6 socket
┌──────────────────────────────▼─────────────────────────────────┐
│            IPv6 NETWORK STACK  (net/ipv6/)                     │
│  MTU = IPV6_MIN_MTU (1280 bytes)                              │
│  6LoWPAN presents a virtual interface with mtu=1280           │
└──────────────────────────────┬─────────────────────────────────┘
                               │  xmit / rcv
┌──────────────────────────────▼─────────────────────────────────┐
│        6LoWPAN VIRTUAL INTERFACE (lowpan0 / bt-pan0)          │
│  ARPHRD_6LOWPAN netdevice                                     │
│  Registered by: lowpan_register_netdevice() (core.c)          │
└──────────────────────────────┬─────────────────────────────────┘
                               │
┌──────────────────────────────▼─────────────────────────────────┐
│            IPHC COMPRESSION  (net/6lowpan/iphc.c)              │
│                                                                 │
│  TX (compress):                                               │
│   lowpan_header_compress()                                    │
│     → strips Traffic Class/Flow Label/Hop Limit if default    │
│     → elides src/dst addr if link-local derivable             │
│     → NHC: compresses UDP ports if in well-known range        │
│                                                                │
│  RX (decompress):                                             │
│   lowpan_header_decompress()                                  │
│     → reconstructs full IPv6 header from IPHC fields          │
│     → expands NHC-compressed next headers                     │
└──────────────────────────────┬─────────────────────────────────┘
                               │
┌──────────────────────────────┤
       ┌───────────────────────┴─────────────────┐
┌──────▼──────────────────┐  ┌────────────────────▼──────────────┐
│  IEEE 802.15.4 BINDING  │  │  BLUETOOTH LE BINDING             │
│  (drivers/net/ieee802154│  │  (net/bluetooth/6lowpan.c)         │
│   + net/ieee802154/)    │  │                                    │
│                         │  │  BLE L2CAP channel 0x23           │
│  Frame: 127-byte max    │  │  Fragmentation per ATT MTU         │
│  ARPHRD_IEEE802154      │  │  EUI-48 → IID mapping              │
│  lowpan_dev.lltype =    │  │  lowpan_dev.lltype =              │
│   LOWPAN_LLTYPE_        │  │   LOWPAN_LLTYPE_BTLE              │
│   IEEE802154            │  │                                    │
└──────────────────────────┘  └───────────────────────────────────┘
                               │ raw frames
┌──────────────────────────────▼─────────────────────────────────┐
│       RADIO HARDWARE  (IEEE 802.15.4 / Bluetooth LE)           │
│   Sub-GHz or 2.4 GHz radio, 127-byte / 23-251-byte frames      │
└────────────────────────────────────────────────────────────────┘
```

---

## IPHC Compression Example

```
Full IPv6 header (40 bytes):
  version=6 tc=0 fl=0 plen=8 nh=17(UDP) hlim=64
  src  = fe80::200:0:0:1  (link-local, EUI-64 from MAC)
  dst  = fe80::200:0:0:2  (link-local, EUI-64 from MAC)

After IPHC compression (3 bytes!):
  IPHC byte 0: TF=11(elide tc+fl) NH=1(NHC) HLIM=10(64) CID=0
  IPHC byte 1: SAC=0 SAM=11(from MAC) M=0 DAC=0 DAM=11(from MAC)
  NHC UDP byte: P=11(elide both ports) C=1(chksum elided)
```

---

## Key Data Structures

| Structure | Purpose |
|---|---|
| `lowpan_dev` | Per-6LoWPAN netdev: link type, IPHC context table |
| `lowpan_iphc_ctx` | Per-context IPHC prefix entry for addr compression |
| `lowpan_nhc` | Registered next-header compression handler |
| `lowpan_addr` | 6LoWPAN address (EUI-64 or short) |

---

## Key Source Files

| File | Purpose |
|---|---|
| `net/6lowpan/core.c` | netdev registration, core module |
| `net/6lowpan/iphc.c` | IPHC compress/decompress |
| `net/6lowpan/nhc.c` | Next-header compression registry |
| `net/6lowpan/nhc_fragment.c` | Fragment header compression |
| `net/6lowpan/ndisc.c` | Neighbor Discovery adaptation |
| `include/net/6lowpan.h` | Public API and data structures |
| `net/bluetooth/6lowpan.c` | BLE 6LoWPAN binding |
| `net/ieee802154/6lowpan/` | 802.15.4 6LoWPAN binding |

---

## Analogy

6LoWPAN is like a **freight compression service for tiny postal vans**:

- Standard IPv6 is like shipping a package with a large address label (40-byte
  header) — fine for big trucks (Ethernet) but too bulky for tiny postal vans
  (802.15.4, 127-byte frames).
- **IPHC** is the compression sticker: "both the sender and receiver know the
  return address, so I'll just write a short code instead."
- **Fragmentation** lets you split a package that won't fit in one van into
  multiple deliveries, then reassemble them at the destination.
- **NHC** compresses the contents label too (UDP ports) — "you always ship to
  CoAP port 5683, so I'll just use code 'C' instead of writing 5683 every time."

---

## References

- `net/6lowpan/` — kernel implementation
- `include/net/6lowpan.h` — API
- RFC 4944 — IPv6 over IEEE 802.15.4
- RFC 6282 — IPHC compression
- RFC 7668 — IPv6 over BLE
- `Documentation/networking/6lowpan.rst`
