# Linux Kernel net/key — PF_KEY v2 (IPsec SA Management)

## Overview

**PF_KEY** implements the PF_KEY v2 protocol (RFC 2367) for managing IPsec
Security Associations (SAs) and Security Policies (SPs) from userspace. It
provides the `AF_KEY` socket family used by IKE daemons (strongSwan, racoon,
pluto) to install, query, and delete IPsec SAs in the kernel's XFRM subsystem.
PF_KEY translates between the SADB message format and the kernel's internal
xfrm_state/xfrm_policy structures.

Source: `net/key/`, `include/uapi/linux/pfkeyv2.h`.

---

## Subsystem Stack

```
┌─────────────────────────────────────────────────────────────────┐
│                        USERSPACE                                │
│                                                                 │
│  strongSwan (charon)    racoon        pluto (Libreswan)         │
│  IKE daemon             IKE daemon    IKE daemon                │
│  socket(AF_KEY, SOCK_RAW, PF_KEY_V2)                           │
└───────────────────────────────┬─────────────────────────────────┘
                                │ PF_KEY v2 messages (SADB_*)
┌───────────────────────────────▼─────────────────────────────────┐
│                   PF_KEY SOCKET LAYER                            │
│                   (net/key/af_key.c)                             │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  struct pfkey_sock  (per-socket state)                  │   │
│  │  - registered[]    SADB message type subscriptions      │   │
│  │  - promisc         receive all SADB messages            │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  pfkey_sendmsg()  — process SADB message from userspace        │
│  pfkey_recvmsg()  — deliver SADB message to userspace          │
└───────────────────────────────┬─────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────┐
│                   PF_KEY MESSAGE PROCESSING                      │
│                   (net/key/af_key.c)                             │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  struct sadb_msg  (SADB wire format header)             │   │
│  │  - sadb_msg_type   (ADD/DELETE/GET/UPDATE/ACQUIRE/…)   │   │
│  │  - sadb_msg_satype (ESP/AH/COMP)                       │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  pfkey_process()  — dispatch to per-type handler               │
│  pfkey_add()      — SADB_ADD: install new SA                   │
│  pfkey_delete()   — SADB_DELETE: remove SA                     │
│  pfkey_get()      — SADB_GET: query SA                         │
│  pfkey_acquire()  — SADB_ACQUIRE: request IKE negotiation      │
└───────────────────────────────┬─────────────────────────────────┘
                                │ pfkey_sadb2xfrm_state()
┌───────────────────────────────▼─────────────────────────────────┐
│                   XFRM SUBSYSTEM                                 │
│                   (net/xfrm/)                                    │
│                                                                 │
│  xfrm_state_add()     — install SA into XFRM SAD              │
│  xfrm_state_delete()  — remove SA from XFRM SAD               │
│  xfrm_policy_insert() — install security policy                │
│                                                                 │
│  IPsec transform: ESP / AH / IPCOMP                            │
└───────────────────────────────┬─────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────┐
│               NETWORK STACK (encrypted/authenticated packets)    │
└─────────────────────────────────────────────────────────────────┘
```

---

## Workflow: Installing an IPsec SA via PF_KEY

```
  IKE daemon negotiates SA parameters
       │
       ▼
  sendmsg(AF_KEY sock, SADB_ADD message)
       │
       ▼
  pfkey_sendmsg()
       │
       ▼
  pfkey_process(hdr)
       │
       ├──► validate SADB message format
       ├──► dispatch: pfkey_add()
       │         │
       │         ▼
       │    pfkey_sadb2xfrm_state()
       │         │
       │         ├──► parse SA parameters (SPI, keys, lifetime)
       │         ├──► allocate xfrm_state
       │         └──► xfrm_state_add()   install in kernel SAD
       │
       └──► broadcast SADB_ADD to registered sockets
```

---

## Key Data Structures

| Structure | Purpose |
|---|---|
| `struct pfkey_sock` | Per-socket PF_KEY state (subscriptions) |
| `struct sadb_msg` | SADB message header (type, SA type, seq) |
| `struct sadb_sa` | Security Association parameters (SPI, auth, encrypt) |
| `struct sadb_address` | Source/destination addresses |
| `struct sadb_key` | Authentication/encryption keys |
| `struct sadb_lifetime` | SA lifetime (bytes, seconds) |

## Key Functions

| Function | Purpose |
|---|---|
| `pfkey_sendmsg()` | Process SADB message from userspace |
| `pfkey_process()` | Dispatch SADB message to handler |
| `pfkey_sadb2xfrm_state()` | Convert SADB SA to kernel xfrm_state |
| `pfkey_add()` | Handle SADB_ADD — install new SA |
| `pfkey_delete()` | Handle SADB_DELETE — remove SA |
| `pfkey_acquire()` | Handle SADB_ACQUIRE — trigger IKE |
| `pfkey_broadcast()` | Broadcast SADB event to subscribers |

## Key Source Files

| File | Purpose |
|---|---|
| `net/key/af_key.c` | PF_KEY v2 socket and message processing |
| `include/uapi/linux/pfkeyv2.h` | SADB message definitions (RFC 2367) |
| `include/net/xfrm.h` | XFRM (IPsec) internal API |

---

## Analogy

PF_KEY is like a **security guard dispatch radio for a building**:

- The **IKE daemon** is the security operations center — it negotiates with
  the other building (remote VPN peer) about what credentials to use.
- **SADB_ADD** is like telling the guard station: "Here's a new ID badge —
  anyone with SPI 0x12345 should be let through with these encryption keys."
- **pfkey_sadb2xfrm_state()** is the guard translating the dispatch message
  into their own logbook format (xfrm_state).
- **SADB_ACQUIRE** is the guard radioing back: "Someone's trying to get in
  but I don't have their credentials — please negotiate access."

---

## References

- `include/uapi/linux/pfkeyv2.h` — SADB message format
- RFC 2367 — PF_KEY v2 specification
- `net/key/af_key.c` — Implementation
- `net/xfrm/` — IPsec transform subsystem
