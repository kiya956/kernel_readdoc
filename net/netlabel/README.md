# NetLabel — Network Packet Security Label Framework

## Overview

**NetLabel** is a Linux kernel framework that provides an **abstraction layer
for network packet security labeling**.  It allows LSM (Linux Security Module)
policies — primarily **SELinux** and **Smack** — to attach security labels to
network packets using standard protocols:

- **CIPSO v4** (Common IP Security Option) — IPv4 option encoding per RFC 1108
- **CALIPSO** (Common Architecture Label IPv6 Security Option) — RFC 5570 for IPv6
- **Unlabeled** — packets without a security label receive a configurable
  default label

The NetLabel API sits between the LSM subsystem (`security/`) and the network
stack (`net/ipv4/`, `net/ipv6/`).  LSMs call the NetLabel kernel API
(`netlbl_kapi.c`) to configure label policies; the network stack calls
`netlbl_skbuff_getattr()` and `netlbl_skbuff_setattr()` to read/write labels
on packets.

Source: `net/netlabel/`, `include/net/netlabel.h`.

---

## Subsystem Stack

```
┌────────────────────────────────────────────────────────────────┐
│                        USERSPACE                               │
│  netlabelctl (libnetlabel)                                     │
│  netlabelctl add domain -t cipso -d example.com               │
│  netlabelctl add map -d example.com -l 2:6                    │
└──────────────────────────────┬─────────────────────────────────┘
                               │  Netlink / netlabel_mgmt.c
┌──────────────────────────────▼─────────────────────────────────┐
│           NETLABEL MANAGEMENT  (netlabel_mgmt.c)               │
│                                                                 │
│  Netlink family: NETLINK_GENERIC "NLBL_MGMT" / "NLBL_CIPSOv4" │
│  Commands: add/remove/list domain maps, DOI definitions        │
│  Domain hash table: netlabel_domainhash.c                     │
│   • Per-domain policy: which protocol + DOI to use            │
│  Address list: netlabel_addrlist.c                            │
│   • Per-address-range policy (overrides domain policy)        │
└──────────────────────────────┬─────────────────────────────────┘
                               │
┌──────────────────────────────▼─────────────────────────────────┐
│           NETLABEL KERNEL API  (netlabel_kapi.c)               │
│                                                                 │
│  netlbl_cfg_*()     — configuration: domain maps, addr lists   │
│  netlbl_catmap_*()  — category bitmap operations               │
│  netlbl_secattr_*() — security attribute lifecycle             │
│                                                                 │
│  Called by LSMs (SELinux / Smack) via:                        │
│   netlbl_skbuff_getattr(skb, family, secattr)                 │
│   netlbl_skbuff_setattr(skb, family, secattr)                 │
│   netlbl_sock_setattr(sk, family, secattr)                    │
└──────────────────────────────┬─────────────────────────────────┘
                               │
┌──────────────────────────────┤
       ┌───────────────────────┼──────────────────────┐
       │                       │                      │
┌──────▼──────────┐  ┌─────────▼───────────┐  ┌──────▼─────────┐
│  CIPSOv4        │  │  CALIPSO             │  │  UNLABELED     │
│  (netlabel_     │  │  (netlabel_calipso.c)│  │  (netlabel_    │
│   cipso_v4.c)   │  │                      │  │  unlabeled.c)  │
│                 │  │  IPv6 Hop-by-Hop     │  │                │
│  IPv4 option    │  │  extension header    │  │ Default label  │
│  DOI + sens/cat │  │  DOI + sens/cat      │  │ for unlabeled  │
│  encoding       │  │  encoding (RFC5570)  │  │ flows          │
└──────┬──────────┘  └─────────┬───────────┘  └──────┬─────────┘
       └───────────────────────┴──────────────────────┘
                               │
┌──────────────────────────────▼─────────────────────────────────┐
│          LINUX NETWORK STACK  (net/ipv4/, net/ipv6/)           │
│  ip_options_build() / ip6_append_data() add/strip label option │
└────────────────────────────────────────────────────────────────┘
```

---

## DOI and Security Attributes

- **DOI** (Domain of Interpretation) — a numeric identifier for a label schema.
  Two systems must agree on a DOI to interpret labels correctly.
- **`netlbl_lsm_secattr`** — the kernel-internal security attribute:
  - `attr.mls.lvl` — MLS sensitivity level
  - `attr.mls.cat` — category bitmap (which compartments)
  - `domain` — optional domain override
- **Category bitmap** — encoded as a sparse bitmap via `netlbl_lsm_catmap`

---

## Key Data Structures

| Structure | Purpose |
|---|---|
| `netlbl_lsm_secattr` | Security label: level + category bitmap + domain |
| `netlbl_lsm_catmap` | Sparse category bitmap (linked list of 240-bit chunks) |
| `netlbl_domhsh_entry` | Per-domain policy entry in the domain hash table |
| `netlbl_af4list` / `netlbl_af6list` | Per-address-range policy override |
| `netlbl_dommap_def` | Per-domain default: protocol + DOI |

---

## Key Source Files

| File | Purpose |
|---|---|
| `net/netlabel/netlabel_kapi.c` | Kernel API for LSMs |
| `net/netlabel/netlabel_cipso_v4.c` | CIPSOv4 encode/decode |
| `net/netlabel/netlabel_calipso.c` | CALIPSO encode/decode |
| `net/netlabel/netlabel_unlabeled.c` | Unlabeled traffic handling |
| `net/netlabel/netlabel_domainhash.c` | Domain → protocol policy table |
| `net/netlabel/netlabel_mgmt.c` | Netlink management interface |
| `include/net/netlabel.h` | Public kernel API |

---

## Analogy

NetLabel is like a **customs declaration system for network packets**:

- Every packet crossing a network boundary needs a **customs label** (CIPSO/CALIPSO
  option) declaring its sensitivity level and compartments.
- The **DOI** is the international customs agreement number: both countries
  (hosts) must speak the same DOI to interpret the label correctly.
- **NetLabel** is the customs officer that reads the label when a packet arrives
  (`netlbl_skbuff_getattr`) and stamps new labels when it leaves
  (`netlbl_skbuff_setattr`).
- **SELinux / Smack** are the immigration officials who decide what to do with
  a traveler based on what the customs label says.

---

## References

- `include/net/netlabel.h` — API
- `net/netlabel/` — implementation
- `Documentation/netlabel/`
- RFC 1108 (CIPSO), RFC 5570 (CALIPSO)
