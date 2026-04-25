# Linux Kernel net/iucv — Inter-User Communication Vehicle

## Overview

**IUCV** (Inter-User Communication Vehicle) implements the z/VM hypervisor
IPC mechanism for IBM s390 mainframes. It provides the `AF_IUCV` socket family
for communication between virtual machines running under z/VM, as well as
HiperSockets-based IUCV transport. IUCV is a message-passing protocol
native to the z/VM environment, allowing guest-to-guest and
guest-to-hypervisor communication.

Source: `net/iucv/`, `include/net/iucv/`.

---

## Subsystem Stack

```
┌─────────────────────────────────────────────────────────────────┐
│                        USERSPACE                                │
│                                                                 │
│  socket(AF_IUCV, SOCK_STREAM/DGRAM, 0)                        │
│  z/VM applications          guest-to-guest IPC                  │
└───────────────────────────────┬─────────────────────────────────┘
                                │ AF_IUCV sockets
┌───────────────────────────────▼─────────────────────────────────┐
│                   AF_IUCV SOCKET LAYER                           │
│                   (net/iucv/af_iucv.c)                           │
│                                                                 │
│  iucv_sock_sendmsg()  — send message via IUCV path             │
│  iucv_sock_recvmsg()  — receive message from IUCV path         │
│  iucv_sock_connect()  — connect to remote VM user              │
│  iucv_sock_accept()   — accept incoming IUCV connection        │
└───────────────────────────────┬─────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────┐
│                   IUCV CORE LAYER                                │
│                   (net/iucv/iucv.c)                              │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  struct iucv_path  (IUCV communication path)            │   │
│  │  - pathid          (path identifier)                    │   │
│  │  - msglim          (message limit)                      │   │
│  │  - handler         (callback handler)                   │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  struct iucv_message  (IUCV message descriptor)         │   │
│  │  - id              (message ID)                         │   │
│  │  - class           (message class)                      │   │
│  │  - length          (message length)                     │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  iucv_path_connect()     — establish path to remote VM         │
│  iucv_path_accept()      — accept incoming path request        │
│  iucv_message_send()     — send message on established path    │
│  iucv_message_receive()  — receive pending message             │
│  iucv_register()         — register IUCV handler               │
└───────────────────────────────┬─────────────────────────────────┘
                                │ CP IUCV instructions (DIAG)
┌───────────────────────────────▼─────────────────────────────────┐
│                   z/VM HYPERVISOR                                 │
│                                                                 │
│  CP IUCV facility                                               │
│  Inter-guest message passing                                    │
│  HiperSockets transport (optional)                              │
└─────────────────────────────────────────────────────────────────┘
```

---

## Workflow: IUCV Message Send

```
  sendmsg(AF_IUCV sock, msg, ...)
       │
       ▼
  iucv_sock_sendmsg()
       │
       ├──► allocate iucv_message
       ├──► copy_from_user()         copy message data
       │
       ▼
  iucv_message_send(path, msg, ...)
       │
       ├──► build IUCV parameter block
       ├──► issue CP IUCV SEND instruction
       │         │
       │         ▼
       │    z/VM hypervisor delivers to target VM
       │
       └──► return completion status
```

---

## Key Data Structures

| Structure | Purpose |
|---|---|
| `struct iucv_path` | IUCV communication path (bidirectional channel) |
| `struct iucv_message` | Message descriptor (ID, class, length) |
| `struct iucv_handler` | Callback handler for path/message events |
| `struct iucv_sock` | AF_IUCV socket private data |

## Key Functions

| Function | Purpose |
|---|---|
| `iucv_message_send()` | Send message on IUCV path |
| `iucv_message_receive()` | Receive message from IUCV path |
| `iucv_path_connect()` | Establish path to remote z/VM user |
| `iucv_path_accept()` | Accept incoming path connection |
| `iucv_path_sever()` | Disconnect IUCV path |
| `iucv_register()` | Register IUCV message handler |

## Key Source Files

| File | Purpose |
|---|---|
| `net/iucv/iucv.c` | Core IUCV path/message operations |
| `net/iucv/af_iucv.c` | AF_IUCV socket implementation |
| `include/net/iucv/iucv.h` | IUCV core API |
| `include/net/iucv/af_iucv.h` | AF_IUCV socket structures |

---

## Analogy

IUCV is like a **pneumatic tube system in a large office building**:

- Each **z/VM guest** is an office on a different floor.
- An **IUCV path** is a pneumatic tube installed between two offices —
  once connected, you can exchange capsules (messages) back and forth.
- **iucv_path_connect()** is the request to install a tube to another office.
- **iucv_message_send()** is putting a message capsule into the tube —
  the hypervisor (building infrastructure) delivers it to the destination.
- **iucv_message_receive()** is opening the capsule that arrived at your desk.
- The **message limit** is how many capsules can be in the tube at once.

---

## References

- `include/net/iucv/iucv.h` — IUCV core API
- `include/net/iucv/af_iucv.h` — AF_IUCV socket API
- `net/iucv/` — Implementation
- z/VM CP Programming Services — IUCV facility reference
