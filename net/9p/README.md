# Linux Kernel net/9p — Plan 9 Filesystem Protocol

## Overview

**9P** (Plan 9 Filesystem Protocol) implements the **9P2000.L** transport layer
for the Linux kernel. It provides a client-side RPC framework to communicate
with 9P servers over multiple transports: **virtio** (for QEMU/KVM virtio-9p
file sharing), **RDMA**, and **TCP/fd**. This enables `v9fs` (the 9P filesystem)
to mount remote directories from a host or across the network.

Source: `net/9p/`, `include/net/9p/`.

---

## Subsystem Stack

```
┌─────────────────────────────────────────────────────────────────┐
│                        USERSPACE                                │
│                                                                 │
│  mount -t 9p ...               v9fs filesystem operations       │
│  9pfs shares (QEMU)            file read/write/stat/walk        │
└───────────────────────────────┬─────────────────────────────────┘
                                │ VFS → v9fs → 9P client calls
┌───────────────────────────────▼─────────────────────────────────┐
│                     V9FS FILESYSTEM LAYER                       │
│                     (fs/9p/)                                     │
│                                                                 │
│  vfs_inode_ops / vfs_file_ops → p9_client_* calls              │
└───────────────────────────────┬─────────────────────────────────┘
                                │ p9_client_rpc()
┌───────────────────────────────▼─────────────────────────────────┐
│                     9P CLIENT LAYER                              │
│                     (net/9p/client.c)                            │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  struct p9_client                                       │   │
│  │  - clnt->trans_mod    (transport module)                │   │
│  │  - clnt->fidpool      (fid allocator)                   │   │
│  │  - clnt->tagpool      (request tag allocator)           │   │
│  │  - clnt->req_list     (outstanding requests)            │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  p9_client_create()     — create client session                │
│  p9_client_rpc()        — send request, wait for reply         │
│  p9_client_walk()       — walk path components                 │
│  p9_client_read/write() — data transfer                        │
└───────────────────────────────┬─────────────────────────────────┘
                                │ p9_trans_module callbacks
┌───────────────────────────────▼─────────────────────────────────┐
│                   TRANSPORT MODULES                              │
│                                                                 │
│  ┌──────────────┐ ┌───────────────┐ ┌──────────────────────┐   │
│  │  trans_virtio │ │  trans_fd     │ │  trans_rdma          │   │
│  │  (virtio-9p)  │ │  (TCP/unix)   │ │  (InfiniBand)       │   │
│  │  net/9p/      │ │  net/9p/      │ │  net/9p/             │   │
│  │  trans_virtio │ │  trans_fd.c   │ │  trans_rdma.c        │   │
│  │  .c           │ │               │ │                      │   │
│  └──────┬───────┘ └───────┬───────┘ └──────────┬───────────┘   │
└─────────┼─────────────────┼────────────────────┼────────────────┘
          │                 │                    │
┌─────────▼─────────────────▼────────────────────▼────────────────┐
│              TRANSPORT (virtio ring / TCP socket / RDMA QP)     │
└─────────────────────────────────────────────────────────────────┘
```

---

## Workflow: 9P Client RPC Request

```
  mount -t 9p ...
       │
       ▼
  v9fs_mount()
       │
       ▼
  p9_client_create()  ──────► select transport module
       │                       (virtio/fd/rdma)
       ▼
  p9_client_rpc(clnt, P9_TATTACH, ...)
       │
       ├──► p9_tag_alloc()         allocate request tag
       ├──► p9_pdu_prepare()       marshal request PDU
       ├──► trans->request()       send via transport
       ├──► wait_event()           sleep until reply
       └──► p9_pdu_parse()         unmarshal response
            │
            ▼
       return result to v9fs
```

---

## Key Data Structures

| Structure | Purpose |
|---|---|
| `struct p9_client` | Client session — transport, fid pool, request tracking |
| `struct p9_req_t` | Single 9P request/response pair with tag |
| `struct p9_trans_module` | Transport module vtable (create, request, close) |
| `struct p9_fid` | File identifier (like NFS file handle) |
| `struct p9_fcall` | Wire-format 9P message (T-message or R-message) |

## Key Functions

| Function | Purpose |
|---|---|
| `p9_client_create()` | Create and initialize a 9P client session |
| `p9_client_rpc()` | Send T-message, wait for R-message response |
| `p9_virtio_request()` | Submit request via virtio transport ring |
| `p9_client_walk()` | Walk path components on 9P server |
| `p9_client_read()` | Read data from remote file |
| `p9_client_write()` | Write data to remote file |
| `p9_tag_alloc()` | Allocate unique tag for request multiplexing |

## Key Source Files

| File | Purpose |
|---|---|
| `net/9p/client.c` | Core 9P client RPC framework |
| `net/9p/trans_virtio.c` | Virtio-9P transport (QEMU/KVM) |
| `net/9p/trans_fd.c` | TCP/unix-socket/fd transport |
| `net/9p/trans_rdma.c` | RDMA transport |
| `net/9p/protocol.c` | PDU marshaling/unmarshaling |
| `include/net/9p/client.h` | Client API structures |
| `include/net/9p/transport.h` | Transport module interface |

---

## Analogy

9P is like a **remote file cabinet with a courier service**:

- The **p9_client** is your office — it keeps track of which drawers (fids) you
  have open and which requests are in flight.
- The **transport module** is the courier — virtio is an express internal mail
  slot (same machine), TCP is postal mail, RDMA is a high-speed pneumatic tube.
- Each **p9_req_t** is a numbered mail ticket — you send a request with a tag
  and the courier brings back the response with the same tag.
- **p9_client_rpc()** is the act of filling out a form, handing it to the
  courier, and waiting at the window for the reply.

---

## References

- `include/net/9p/client.h` — Client API
- `include/net/9p/transport.h` — Transport interface
- `Documentation/filesystems/9p.rst` — 9P filesystem docs
- `net/9p/` — Implementation
