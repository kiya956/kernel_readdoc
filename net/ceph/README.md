# Linux Kernel net/ceph — Ceph Messenger Protocol

## Overview

**Ceph messenger** (`libceph`) implements the kernel client-side networking for
the Ceph distributed storage system. It handles the **messenger v1/v2 protocol**
for communicating with Ceph MON (monitor), OSD (object storage daemon), and MDS
(metadata server) daemons. The kernel module is used by CephFS (`fs/ceph/`) and
RBD (`drivers/block/rbd.c`) to access Ceph clusters.

Source: `net/ceph/`, `include/linux/ceph/`.

---

## Subsystem Stack

```
┌─────────────────────────────────────────────────────────────────┐
│                        USERSPACE                                │
│                                                                 │
│  mount -t ceph ...          rbd map ...          ceph CLI       │
└───────────────────────────────┬─────────────────────────────────┘
                                │ VFS / block layer
┌───────────────────────────────▼─────────────────────────────────┐
│                   CEPH CLIENTS                                   │
│                                                                 │
│  ┌──────────────┐ ┌───────────────┐                             │
│  │  CephFS      │ │  RBD          │                             │
│  │  (fs/ceph/)  │ │  (drivers/    │                             │
│  │              │ │  block/rbd.c) │                             │
│  └──────┬───────┘ └───────┬───────┘                             │
└─────────┼─────────────────┼─────────────────────────────────────┘
          │                 │  libceph API calls
┌─────────▼─────────────────▼─────────────────────────────────────┐
│                   LIBCEPH CORE                                   │
│                   (net/ceph/)                                    │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  struct ceph_messenger                                  │   │
│  │  - inst (entity address)                                │   │
│  │  - supported_features                                   │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  struct ceph_connection  (per-peer connection)           │   │
│  │  - state machine (CLOSED → CONNECTING → OPEN)           │   │
│  │  - out_msg / in_msg   (current send/recv message)       │   │
│  │  - sock               (underlying TCP socket)           │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  struct ceph_msg (protocol message)                     │   │
│  │  - hdr (type, front_len, middle_len, data_len)          │   │
│  │  - front / middle / data (scatter-gather segments)      │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  ceph_con_send()              — queue message for send         │
│  ceph_msg_data_add_pages()    — attach page data to message    │
│  ceph_monc_init()             — initialize monitor client      │
│  ceph_osdc_init()             — initialize OSD client          │
└───────────────────────────────┬─────────────────────────────────┘
                                │ TCP sockets
┌───────────────────────────────▼─────────────────────────────────┐
│               NETWORK (TCP to Ceph MON/OSD/MDS daemons)         │
└─────────────────────────────────────────────────────────────────┘
```

---

## Workflow: Sending a Ceph Message

```
  CephFS/RBD operation
       │
       ▼
  ceph_msg_new(type, front_len)
       │
       ├──► allocate ceph_msg
       ├──► fill front (header) payload
       ├──► ceph_msg_data_add_pages()   attach data pages
       │
       ▼
  ceph_con_send(con, msg)
       │
       ├──► queue msg on con->out_queue
       ├──► queue_con(con)              schedule workqueue
       │
       ▼
  con_work()  (messenger workqueue)
       │
       ├──► write_partial_message_data()
       ├──► kernel_sendmsg()            TCP send
       └──► advance state machine
```

---

## Key Data Structures

| Structure | Purpose |
|---|---|
| `struct ceph_connection` | Per-peer connection with state machine |
| `struct ceph_messenger` | Messenger instance (entity, features) |
| `struct ceph_msg` | Protocol message with header + data segments |
| `struct ceph_client` | Top-level Ceph client (options, monc, osdc) |
| `struct ceph_msg_data` | Message data segment (pages, pagelist, bio) |

## Key Functions

| Function | Purpose |
|---|---|
| `ceph_con_send()` | Queue a message for transmission on connection |
| `ceph_msg_data_add_pages()` | Attach page array data to message |
| `ceph_con_open()` | Open connection to a Ceph entity |
| `ceph_con_close()` | Close a Ceph connection |
| `ceph_msg_new()` | Allocate new Ceph protocol message |
| `ceph_monc_init()` | Initialize monitor client |
| `ceph_osdc_init()` | Initialize OSD client |

## Key Source Files

| File | Purpose |
|---|---|
| `net/ceph/messenger.c` | Core connection state machine |
| `net/ceph/messenger_v2.c` | Messenger v2 protocol (msgr2) |
| `net/ceph/messenger_v1.c` | Legacy messenger v1 protocol |
| `net/ceph/mon_client.c` | Monitor client (cluster map) |
| `net/ceph/osd_client.c` | OSD client (object I/O) |
| `net/ceph/ceph_common.c` | Common client setup, options |
| `net/ceph/auth.c` | Authentication (cephx, none) |
| `include/linux/ceph/messenger.h` | Messenger API |

---

## Analogy

Ceph messenger is like a **postal system for a distributed warehouse**:

- The **ceph_connection** is a mail route to a specific warehouse (OSD) or
  headquarters (MON) — it maintains a persistent channel with delivery tracking.
- A **ceph_msg** is a parcel — it has a header label (type, destination) and
  can carry attached data pages (the goods).
- **ceph_con_send()** is dropping the parcel at the outgoing mailbox — a
  background worker picks it up and delivers it via TCP.
- The **state machine** handles the handshake — it's like establishing a
  secure line before exchanging parcels.

---

## References

- `include/linux/ceph/messenger.h` — Messenger API
- `include/linux/ceph/ceph_features.h` — Feature negotiation
- `Documentation/filesystems/ceph.rst` — CephFS docs
- `net/ceph/` — Implementation
