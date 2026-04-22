# NSH — Network Service Header

## Overview

**NSH (Network Service Header)**, defined in RFC 8300, is a protocol
**encapsulation header** used in **Service Function Chaining (SFC)**.  It
carries metadata that enables ordered delivery of packets through a sequence
of network services (firewalls, NAT, DPI, load balancers) without requiring
per-service policy at each hop.

The Linux kernel `net/nsh/` module provides:
- **`nsh_push()`** — prepend an NSH header to an skb
- **`nsh_pop()`** — remove the NSH header and restore the inner protocol
- Integration with OvS (Open vSwitch), TC, and Geneve/VXLAN-GPE tunnels

NSH is identified by Ethertype `0x894F`.

Source: `net/nsh/nsh.c`, `include/net/nsh.h`.

---

## Subsystem Stack

```
┌────────────────────────────────────────────────────────────────┐
│                     SERVICE FUNCTION CHAIN                     │
│  OvS / TC rules control NSH push/pop per SFC path             │
│  SFC Proxy or NSH-aware switch decides forwarding             │
└──────────────────────────────┬─────────────────────────────────┘
                               │  OvS actions / TC action act_mpls
┌──────────────────────────────▼─────────────────────────────────┐
│          NSH OPERATIONS  (net/nsh/nsh.c)                       │
│                                                                 │
│  nsh_push(skb, &nshhdr)                                       │
│   → skb_cow_head() — make room in skb headroom                 │
│   → skb_push() — advance data pointer                         │
│   → copy nshhdr into skb                                      │
│   → set skb->protocol = ETH_P_NSH (0x894F)                    │
│   → reset mac/network headers                                  │
│                                                                 │
│  nsh_pop(skb)                                                  │
│   → read NSH header, determine inner_proto from nh->np        │
│   → tun_p_to_eth_p() — convert Next Protocol to Ethertype     │
│   → __skb_pull() — remove NSH header                          │
│   → skb_postpull_rcsum() — fix checksum                       │
│   → restore skb->protocol to inner protocol                   │
└──────────────────────────────┬─────────────────────────────────┘
                               │
┌──────────────────────────────▼─────────────────────────────────┐
│                    NSH FRAME ON WIRE                           │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ Outer transport (Geneve/VXLAN-GPE/Ethernet/GRE)          │  │
│  ├──────────────────────────────────────────────────────────┤  │
│  │ NSH Base Header (4 bytes):                               │  │
│  │   Version=0  OAM=0  TTL=63  Length  MD-type  NP         │  │
│  ├──────────────────────────────────────────────────────────┤  │
│  │ Service Path Header (4 bytes):                           │  │
│  │   Service Path ID (SPI, 24 bits)                         │  │
│  │   Service Index (SI, 8 bits) — decremented at each hop  │  │
│  ├──────────────────────────────────────────────────────────┤  │
│  │ Optional Metadata (MD-type 1: fixed 16 bytes;            │  │
│  │                    MD-type 2: variable TLVs)             │  │
│  ├──────────────────────────────────────────────────────────┤  │
│  │ Original Payload (Ethernet / IPv4 / IPv6 per NP field)   │  │
│  └──────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────┘
```

---

## Service Function Chaining Example

```
Client → Classifier → [SPI=100, SI=3] →
  SF1 (Firewall):    SI decrements to 2 → [SPI=100, SI=2] →
  SF2 (IDS):         SI decrements to 1 → [SPI=100, SI=1] →
  SF3 (Load Balancer): SI decrements to 0 → payload delivered
```

The `SI` (Service Index) acts as a "stamps remaining" counter.  Each service
decrements it; when it reaches 0 or the path is complete, the payload is
forwarded or dropped.

---

## Key Data Structures

| Structure | Purpose |
|---|---|
| `nshhdr` | Full NSH header: base + service path + optional metadata |
| `nsh_base_hdr` | 4-byte base: version, OAM, TTL, length, MD type, NP |
| `nsh_md1_ctx` | MD-type 1: 4 fixed 32-bit context words |
| `nsh_md2_tlv_hdr` | MD-type 2: TLV header for variable metadata |

---

## Key Source Files

| File | Purpose |
|---|---|
| `net/nsh/nsh.c` | nsh_push, nsh_pop, nsh_hdr_len |
| `include/net/nsh.h` | All NSH structures and inline helpers |
| `net/openvswitch/actions.c` | OvS push_nsh / pop_nsh action |

---

## Analogy

NSH is like a **priority boarding pass for network packets**:

- The **SPI (Service Path ID)** is the flight number — it determines which
  sequence of lounges (service functions) the passenger must visit.
- The **SI (Service Index)** is the stamp counter on the pass: each lounge
  stamps it (decrements); when all stamps are used, the passenger boards.
- Each service function (lounge) reads the pass, does its work (firewall
  check, DPI scan, load-balance decision), and hands the passenger on with
  one fewer stamp.
- **NSH push** is the check-in desk that wraps the passenger in the boarding
  system; **NSH pop** is the gate agent that removes the boarding pass when
  the passenger reaches the plane.

---

## References

- `net/nsh/nsh.c` — implementation
- `include/net/nsh.h` — all structures
- RFC 8300 — NSH specification
- RFC 7665 — SFC architecture
