# net/handshake — In-Kernel TLS Handshake Upcall

## Overview

`net/handshake/` implements the **kernel TLS handshake upcall** mechanism
(merged in Linux 6.2).  It allows kernel subsystems (NFS, NVME-TCP, SMB, etc.)
that need a **TLS-encrypted socket** to delegate the TLS handshake to a
**userspace daemon** (`tlshd` from the ktls-utils project) via a Generic
Netlink interface, without building a full TLS stack in the kernel.

The flow:
1. Kernel code calls `tls_client_hello_x509()` or `tls_server_hello_x509()`.
2. `net/handshake` enqueues a handshake request and sends a Netlink notification
   to the `tlshd` group.
3. `tlshd` accepts the request (via `HANDSHAKE_CMD_ACCEPT`), receives the socket
   fd, performs the TLS handshake using OpenSSL/GnuTLS, then calls
   `HANDSHAKE_CMD_DONE` to report success/failure.
4. The kernel resumes the waiting caller with a TLS-ready socket.

Source: `net/handshake/`, `include/uapi/linux/handshake.h`.

---

## Subsystem Stack

```
┌────────────────────────────────────────────────────────────────┐
│                  USERSPACE DAEMON  (tlshd)                     │
│                                                                 │
│  Subscribes to HANDSHAKE_NLGRP_TLSHD multicast group           │
│  Receives HANDSHAKE_CMD_READY notification                     │
│  Sends HANDSHAKE_CMD_ACCEPT  → gets sock fd + peer identity    │
│  Performs TLS handshake (OpenSSL / GnuTLS / NSS)              │
│  Sends HANDSHAKE_CMD_DONE   → status + remote auth info        │
└──────────────────────────────┬─────────────────────────────────┘
                               │  Generic Netlink  (handshake family)
┌──────────────────────────────▼─────────────────────────────────┐
│            NETLINK INTERFACE  (netlink.c, genl.c)              │
│                                                                 │
│  genl family: HANDSHAKE_FAMILY_NAME = "handshake"              │
│  HANDSHAKE_CMD_ACCEPT — daemon accepts a pending request       │
│    → kernel sends sock fd via SCM_RIGHTS ancillary data        │
│    → includes: timeout, auth mode, peer identity hint          │
│  HANDSHAKE_CMD_DONE   — daemon signals handshake completion    │
│    → kernel resumes the waiting caller (completion)            │
│                                                                 │
│  Multicast groups:                                             │
│    HANDSHAKE_NLGRP_NONE  — no subscribers                     │
│    HANDSHAKE_NLGRP_TLSHD — tlshd subscribes here              │
└──────────────────────────────┬─────────────────────────────────┘
                               │
┌──────────────────────────────▼─────────────────────────────────┐
│            HANDSHAKE REQUEST  (request.c)                      │
│                                                                 │
│  struct handshake_req: pending request state                   │
│   hr_sk      — the socket needing TLS                         │
│   hr_proto   — protocol ops (tls_handshake_req_alloc, etc.)   │
│   hr_done    — completion callback                            │
│   hr_rhash   — rhashtable entry (sock → req lookup)           │
│                                                                 │
│  handshake_req_hash_lookup(sk) → find pending req for socket  │
│  handshake_req_submit()        → enqueue + notify tlshd       │
│  handshake_req_cancel()        → cancel a pending request     │
└──────────────────────────────┬─────────────────────────────────┘
                               │
┌──────────────────────────────▼─────────────────────────────────┐
│            TLS PROTOCOL OPS  (tlshd.c)                        │
│                                                                 │
│  tls_client_hello_x509()   — initiate client TLS (X.509)      │
│  tls_client_hello_psk()    — initiate client TLS (PSK)        │
│  tls_server_hello_x509()   — initiate server TLS (X.509)      │
│  tls_server_hello_psk()    — initiate server TLS (PSK)        │
│                                                                 │
│  After successful DONE: socket is upgraded to ktls             │
└──────────────────────────────┬─────────────────────────────────┘
                               │
┌──────────────────────────────▼─────────────────────────────────┐
│         KERNEL TLS (net/tls/)  — ktls sendmsg/recvmsg          │
│         NFS / NVME-TCP / SMB  — use the TLS socket             │
└────────────────────────────────────────────────────────────────┘
```

---

## Handshake Request Lifetime

```
Kernel subsystem (e.g., NFS):
  tls_client_hello_x509(sock, &args) ─► handshake_req_submit()
                                              │
                                     Notifies NLGRP_TLSHD
                                              │
                                    tlshd: ACCEPT → gets sock fd
                                              │
                                    tlshd does TLS handshake
                                              │
                                    tlshd: DONE (status=OK)
                                              │
                                    Kernel: calls hr_done(req, err)
                                              │
                              NFS resumes with TLS-ready socket
```

---

## Key Data Structures

| Structure | Purpose |
|---|---|
| `handshake_req` | Pending request: sock, proto ops, completion callback |
| `handshake_proto` | Per-protocol vtable (TLS client/server X509/PSK) |
| `tls_handshake_args` | Caller-provided: timeout, auth mode, cert/key info |

---

## Key Source Files

| File | Purpose |
|---|---|
| `net/handshake/request.c` | Request lifecycle, rhashtable |
| `net/handshake/netlink.c` | ACCEPT/DONE Netlink handlers |
| `net/handshake/genl.c` | Generic Netlink family definition (auto-generated) |
| `net/handshake/tlshd.c` | TLS-specific handshake request ops |
| `net/handshake/alert.c` | TLS alert handling |
| `include/uapi/linux/handshake.h` | UAPI: Netlink commands + attrs |
| `include/net/handshake.h` | Internal API for kernel callers |

---

## Analogy

The kernel handshake subsystem is like a **notary public service**:

- The kernel (e.g., NFS) has a document (socket) that needs a certified
  signature (TLS certificate exchange) but doesn't have the notary's tools.
- It **drops off the document** (enqueues handshake_req, notifies tlshd).
- The **notary** (`tlshd`) picks it up, performs the legally required process
  (TLS handshake using the system's certificate store), stamps it
  (upgrades socket to ktls), and sends back the result.
- The kernel is **asynchronously notified** when the notarization is done,
  and can then use the certified channel (TLS socket) freely.

---

## References

- `net/handshake/` — full implementation
- `include/uapi/linux/handshake.h` — UAPI
- https://github.com/oracle/ktls-utils — tlshd daemon
- `Documentation/networking/tls-handshake.rst`
