# IFE — Inter-FE (Forwarding Element) Metadata Protocol

## Overview

**IFE (Inter-FE protocol)** is a Linux kernel networking module
(`net/ife/`) that implements a simple **TLV-based metadata encapsulation
protocol** for carrying per-packet metadata between forwarding elements in
a software-defined networking (SDN) pipeline.

IFE is based on the **IETF ForCES** (Forwarding and Control Element Separation)
working group's Inter-FE LFB (Logical Function Block) draft.  It is used
primarily by the **Linux TC (Traffic Control)** subsystem — specifically the
`act_ife` TC action — to attach metadata TLVs to Ethernet frames as they
traverse multiple TC pipeline stages (e.g., between hosts in an OVS+TC setup).

The IFE frame format wraps an existing Ethernet frame with:
1. A copy of the **outer Ethernet header** (to maintain forwarding)
2. A **total-metadata-length** field (2 bytes)
3. One or more **TLV records** (Type + Length + Value metadata items)
4. The **original Ethernet frame** (payload)

Source: `net/ife/ife.c`, `include/net/ife.h`.

---

## Subsystem Stack

```
┌────────────────────────────────────────────────────────────────┐
│              TC PIPELINE  (userspace tc tool)                  │
│  tc filter add dev eth0 ... action ife encode \                │
│             use mark dst 01:02:03:04:05:06                    │
└──────────────────────────────┬─────────────────────────────────┘
                               │  TC action: act_ife
┌──────────────────────────────▼─────────────────────────────────┐
│             TC ACT_IFE  (net/sched/act_ife.c)                  │
│                                                                 │
│  Encode action (TX):                                           │
│   1. ife_encode(skb, metalen) — push IFE header                │
│   2. For each metadata type: ife_tlv_meta_encode(skb, type, v) │
│   3. ife_encode_meta_u16/u32() — write TLV into header        │
│                                                                 │
│  Decode action (RX):                                           │
│   1. ife_decode(skb, metalen) — strip IFE header               │
│   2. ife_tlv_meta_next()     — iterate TLVs                    │
│   3. ife_decode_meta_u16/u32() — read TLV values              │
└──────────────────────────────┬─────────────────────────────────┘
                               │
┌──────────────────────────────▼─────────────────────────────────┐
│             IFE CORE  (net/ife/ife.c)                          │
│                                                                 │
│  ife_encode(skb, metalen)                                      │
│   → pushes outer Ethernet + IFE header onto skb               │
│   → returns pointer to TLV region for caller to populate       │
│                                                                 │
│  ife_decode(skb, metalen)                                      │
│   → pulls IFE header off skb                                   │
│   → returns pointer to TLV region                             │
│                                                                 │
│  ife_tlv_meta_encode(skb_data, type, dlen, dval)               │
│   → writes one TLV at the given offset                         │
│                                                                 │
│  ife_tlv_meta_next(skb_data, remaining)                        │
│   → advances to next TLV in the TLV region                    │
└──────────────────────────────┬─────────────────────────────────┘
                               │  Ethernet frame with IFE header
┌──────────────────────────────▼─────────────────────────────────┐
│              NETWORK  (between TC-enabled hosts/switches)       │
└────────────────────────────────────────────────────────────────┘
```

---

## IFE Frame Format

```
┌─────────────────────────────────────────────────────────────┐
│ Outer Ethernet Header (14 bytes, dst=IFE multicast/unicast) │
├─────────────────────────────────────────────────────────────┤
│ IFE Header: metalen (2 bytes, total TLV region size)        │
├─────────────────────────────────────────────────────────────┤
│ TLV 1: type (2 bytes) | len (2 bytes) | value               │
│ TLV 2: type (2 bytes) | len (2 bytes) | value               │
│ ...                                                         │
├─────────────────────────────────────────────────────────────┤
│ Original Ethernet Frame (inner header + payload)            │
└─────────────────────────────────────────────────────────────┘
```

---

## Key Source Files

| File | Purpose |
|---|---|
| `net/ife/ife.c` | encode/decode, TLV iteration |
| `include/net/ife.h` | API: ife_encode, ife_decode, TLV helpers |
| `net/sched/act_ife.c` | TC action that uses the IFE library |

---

## Analogy

IFE is like a **shipping label system for network packets**:

- Each packet gets an **outer wrapper** (IFE header) that acts like a shipping
  label with multiple sticky notes attached (TLVs).
- Each sticky note carries a piece of metadata (e.g., "mark=5", "skb priority=3").
- When the packet arrives at the receiving TC stage, it **peels off** the outer
  wrapper and reads the sticky notes to make forwarding decisions.
- The actual letter (original Ethernet frame) is carried intact inside the wrapper.

---

## References

- `net/ife/ife.c` — implementation
- `include/net/ife.h` — API
- `net/sched/act_ife.c` — primary user (TC act_ife)
- IETF ForCES draft-ietf-forces-interfelfb
