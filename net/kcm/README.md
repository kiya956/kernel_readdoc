# Linux Kernel net/kcm — Kernel Connection Multiplexor

## Overview

**KCM** (Kernel Connection Multiplexor) multiplexes application-level protocol
messages over TCP connections. It uses **BPF programs** to detect message
boundaries in the byte stream, allowing multiple application sockets to share
a pool of underlying TCP connections. KCM is useful for protocols that define
message framing (length-prefixed, delimiter-based) where the kernel can
efficiently demux messages to the right consumer.

Source: `net/kcm/`, `include/net/kcm.h`.

---

## Subsystem Stack

```
┌─────────────────────────────────────────────────────────────────┐
│                        USERSPACE                                │
│                                                                 │
│  socket(AF_KCM, SOCK_DGRAM/SEQPACKET, KCMPROTO_CONNECTED)     │
│  Application sends/receives complete protocol messages          │
└───────────────────────────────┬─────────────────────────────────┘
                                │ AF_KCM sockets (message-oriented)
┌───────────────────────────────▼─────────────────────────────────┐
│                   KCM SOCKET LAYER                               │
│                   (net/kcm/kcmsock.c)                            │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  struct kcm_sock  (per-socket KCM state)                │   │
│  │  - mux          (shared multiplexor)                    │   │
│  │  - rx_psock     (currently attached receive psock)       │   │
│  │  - tx_psock     (currently attached transmit psock)      │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  kcm_sendmsg()  — send complete message over TCP pool          │
│  kcm_recvmsg()  — receive complete message from TCP pool       │
└───────────────────────────────┬─────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────┐
│                   KCM MULTIPLEXOR                                │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  struct kcm_mux  (connection pool multiplexor)          │   │
│  │  - kcm_socks[]   list of KCM sockets                   │   │
│  │  - psocks[]       list of underlying TCP connections    │   │
│  │  - rx_wait_cnt    sockets waiting for data              │   │
│  └─────────────────────────────────────────────────────────┘   │
└───────────────────────────────┬─────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────┐
│                   KCM PSOCK (Protocol Socket)                    │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  struct kcm_psock  (attached TCP connection)            │   │
│  │  - sk             (underlying TCP socket)               │   │
│  │  - bpf_prog       (message boundary detector)           │   │
│  │  - strp            (stream parser)                      │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  kcm_attach()  — attach TCP socket with BPF to multiplexor     │
│  strp_data_ready() — stream parser detects message boundary    │
└───────────────────────────────┬─────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────┐
│               TCP CONNECTIONS (byte stream transport)            │
└─────────────────────────────────────────────────────────────────┘
```

---

## Workflow: KCM Message Receive

```
  TCP data arrives on psock
       │
       ▼
  strp_data_ready()
       │
       ├──► run BPF program to find message length
       ├──► accumulate bytes until complete message
       │
       ▼
  kcm_rcv_strparser()
       │
       ├──► find waiting KCM socket (round-robin)
       ├──► attach message skb to kcm_sock rx queue
       └──► wake_up(kcm_sock)
            │
            ▼
       kcm_recvmsg() → deliver to application
```

---

## Key Data Structures

| Structure | Purpose |
|---|---|
| `struct kcm_sock` | Per-socket KCM state (mux attachment, rx/tx psock) |
| `struct kcm_psock` | Attached TCP connection with BPF parser |
| `struct kcm_mux` | Multiplexor — pool of KCM sockets and psocks |
| `struct strparser` | Stream parser for message boundary detection |

## Key Functions

| Function | Purpose |
|---|---|
| `kcm_sendmsg()` | Send complete protocol message |
| `kcm_recvmsg()` | Receive complete protocol message |
| `kcm_attach()` | Attach TCP socket + BPF prog to multiplexor |
| `kcm_unattach()` | Detach TCP socket from multiplexor |
| `kcm_rcv_strparser()` | Handle parsed message from stream parser |
| `kcm_tx_work()` | Workqueue handler for transmit |

## Key Source Files

| File | Purpose |
|---|---|
| `net/kcm/kcmsock.c` | KCM socket and multiplexor implementation |
| `net/kcm/kcmproc.c` | /proc/net/kcm interface |
| `include/net/kcm.h` | KCM data structures |
| `include/uapi/linux/kcm.h` | Userspace ABI |

---

## Analogy

KCM is like a **mail room that sorts packages from conveyor belts**:

- The **TCP connections** (psocks) are conveyor belts carrying a continuous
  stream of packages (bytes).
- The **BPF program** is the scanner that reads package labels to detect where
  one package ends and the next begins.
- The **multiplexor** is the sorting room — it takes complete packages from
  any conveyor belt and assigns them to the right **mailbox** (KCM socket).
- Each **KCM socket** is a mailbox — the application opens it and gets
  complete, framed messages, never partial reads.

---

## References

- `include/net/kcm.h` — KCM API
- `include/uapi/linux/kcm.h` — Userspace ABI
- `net/kcm/` — Implementation
- `Documentation/networking/kcm.rst` — KCM documentation
