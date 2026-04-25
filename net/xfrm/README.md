# XFRM — IPsec Transform Framework

## Overview

**XFRM** (pronounced "transform") is the Linux kernel's **IPsec framework**,
implementing Security Associations (SA), Security Policies (SP), and the
ESP/AH/IPCOMP transforms for encrypted and authenticated IP communication.

Key components:
- **Security Association (SA)** — per-flow crypto state: algorithm, keys,
  SPI, sequence numbers, replay window
- **Security Policy (SP)** — rules matching traffic to SAs: selectors
  (src/dst/proto/port) → template → SA lookup
- **Transforms** — ESP (Encapsulating Security Payload), AH (Authentication
  Header), IPCOMP (IP Compression)
- **XFRM state machine** — SA lifecycle: VALID, ACQ, EXPIRED, DEAD
- **Netlink (XFRM_MSG_*)** — userspace IKE daemons (strongSwan, Libreswan)
  manage SAs/SPs via xfrm_user netlink interface

Source: `net/xfrm/`, `include/net/xfrm.h`.

---

## Subsystem Stack

```
┌──────────────────────────────────────────────────────────────────┐
│                    IKE DAEMON (userspace)                         │
│  strongSwan / Libreswan / iproute2 (ip xfrm)                    │
│  ↕ XFRM Netlink (XFRM_MSG_NEWSA, XFRM_MSG_NEWPOLICY, …)       │
└──────────────────────────────┬───────────────────────────────────┘
                               │
┌──────────────────────────────▼───────────────────────────────────┐
│              XFRM POLICY DATABASE  (net/xfrm/xfrm_policy.c)     │
│                                                                   │
│  xfrm_policy_lookup()  — match packet against SP database        │
│  xfrm_lookup()         — full policy + SA resolution for output  │
│  xfrm_policy_insert()  — add new policy from netlink             │
│                                                                   │
│  struct xfrm_policy:                                             │
│    selector (src/dst/proto/ports) → xfrm_tmpl[] → SA lookup     │
└──────────────────────────────┬───────────────────────────────────┘
                               │ template matching
┌──────────────────────────────▼───────────────────────────────────┐
│              XFRM STATE (SA) DATABASE  (net/xfrm/xfrm_state.c)  │
│                                                                   │
│  xfrm_state_find()    — find SA matching policy template         │
│  xfrm_state_alloc()   — allocate new SA                          │
│  xfrm_replay_check()  — anti-replay window verification          │
│                                                                   │
│  struct xfrm_state:                                              │
│    id (daddr, SPI, proto) + crypto algo + keys + replay state    │
│    lifetime (byte/packet/time limits) + encap mode               │
└──────────────────────────────┬───────────────────────────────────┘
                               │
┌──────────────────────────────▼───────────────────────────────────┐
│              XFRM INPUT / OUTPUT  (net/xfrm/xfrm_input.c,       │
│                                    net/xfrm/xfrm_output.c)      │
│                                                                   │
│  Outbound:                                                       │
│    packet → xfrm_lookup() → SA match → xfrm_output()            │
│    → ESP encrypt + HMAC → new IP header → dev_queue_xmit         │
│                                                                   │
│  Inbound:                                                        │
│    packet → ip_rcv → ESP detected → xfrm_input()                │
│    → ESP decrypt + HMAC verify → replay check → deliver          │
└──────────────────────────────┬───────────────────────────────────┘
                               │
┌──────────────────────────────▼───────────────────────────────────┐
│                      IP LAYER (IPv4/IPv6)                        │
│  xfrm hooks in ip_output / ip6_output for policy enforcement     │
│  xfrm4_rcv / xfrm6_rcv for inbound ESP/AH processing            │
└──────────────────────────────────────────────────────────────────┘
```

---

## IPsec Packet Flow

```
  OUTBOUND (encrypt):
    App data → TCP/UDP → IP output
      → xfrm_lookup(): policy match? → find SA
      → xfrm_output(): ESP transform
        → encrypt payload (AES-GCM / AES-CBC + HMAC)
        → prepend ESP header (SPI + SeqNo)
        → new outer IP header (tunnel mode) or modify (transport)
      → dev_queue_xmit()

  INBOUND (decrypt):
    NIC → ip_rcv() → proto=ESP(50)
      → xfrm_input(): SA lookup by (dst, SPI, proto)
        → ESP decrypt + integrity verify
        → xfrm_replay_check() — anti-replay
        → xfrm_policy_check() — verify policy allows this
      → deliver to upper layer
```

---

## Key Data Structures

| Structure | Purpose |
|---|---|
| `struct xfrm_state` | Security Association: SPI, keys, algo, replay state |
| `struct xfrm_policy` | Security Policy: selector → templates → SA resolution |
| `struct xfrm_tmpl` | Template in policy: desired proto/mode/algo for SA lookup |
| `struct xfrm_selector` | Traffic selector: src/dst addr/port/proto match |
| `struct xfrm_lifetime_cfg` | SA lifetime limits: bytes, packets, time |
| `struct xfrm_replay_state` | Anti-replay bitmap and sequence tracking |
| `struct xfrm_dst` | Cached route + SA bundle for fast-path output |

---

## Key Functions

| Function | Role |
|---|---|
| `xfrm_lookup()` | Output path: policy lookup + SA resolution + bundle |
| `xfrm_input()` | Input path: SA lookup, decrypt, replay check |
| `xfrm_output()` | Apply outbound transform (ESP/AH encrypt) |
| `xfrm_state_find()` | Find SA matching a policy template |
| `xfrm_state_alloc()` | Allocate new xfrm_state (SA) |
| `xfrm_policy_lookup()` | Search policy database for matching selector |
| `xfrm_policy_insert()` | Insert new policy (from netlink/IKE daemon) |
| `xfrm_sk_policy_insert()` | Per-socket IPsec policy |
| `xfrm_replay_check()` | Anti-replay window verification |

---

## Key Source Files

| File | Purpose |
|---|---|
| `net/xfrm/xfrm_policy.c` | Policy database: lookup, insert, delete |
| `net/xfrm/xfrm_state.c` | SA database: find, alloc, expire |
| `net/xfrm/xfrm_input.c` | Inbound ESP/AH processing |
| `net/xfrm/xfrm_output.c` | Outbound ESP/AH transform |
| `net/xfrm/xfrm_user.c` | Netlink interface for IKE daemons |
| `net/xfrm/xfrm_replay.c` | Anti-replay window logic |
| `net/ipv4/esp4.c` | ESP transform for IPv4 |
| `net/ipv6/esp6.c` | ESP transform for IPv6 |
| `include/net/xfrm.h` | Core structures and declarations |

---

## Analogy

XFRM is like a **diplomatic pouch system for network packets**:

- The **Security Policy (SP)** is the rule book at the embassy gate: "all
  mail to country X on topic Y must go in a sealed diplomatic pouch."
- The **Security Association (SA)** is the specific sealed pouch: it knows
  the lock combination (encryption key), the wax seal formula (HMAC key),
  and has a serial number (SPI) so the receiving embassy finds the right key.
- **xfrm_output()** is the clerk who puts the letter in the pouch, locks it,
  and stamps the serial number on the outside.
- **xfrm_input()** is the clerk at the receiving embassy who checks the serial
  number, verifies the seal, unlocks the pouch, and delivers the letter.
- The **replay window** is the serial number log that catches duplicate or
  replayed pouches — if you've already received serial #42, reject another #42.

---

## References

- `net/xfrm/` — kernel implementation
- `include/net/xfrm.h` — core structures
- RFC 4301 — IPsec Architecture
- RFC 4303 — ESP (Encapsulating Security Payload)
- RFC 4302 — AH (Authentication Header)
- `Documentation/networking/xfrm_device.rst`
