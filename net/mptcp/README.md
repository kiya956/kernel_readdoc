# Linux Kernel MPTCP — Multipath TCP

## Overview

**MPTCP** (Multipath TCP, RFC 8684) allows a single TCP connection to use
**multiple network paths simultaneously** — e.g., Wi-Fi + LTE at the same time.
The Linux kernel MPTCP implementation lives in `net/mptcp/`. It presents a
standard `SOCK_STREAM` socket to userspace while transparently managing multiple
**subflows** (ordinary TCP connections) underneath.

---

## Subsystem Stack

```
┌─────────────────────────────────────────────────────────────────┐
│                        USERSPACE                                │
│                                                                 │
│   socket(AF_INET, SOCK_STREAM, IPPROTO_MPTCP)                  │
│   connect() / send() / recv() / setsockopt(MPTCP_*)            │
│                                                                 │
│   pm_nl_ctl / ss -M / ip mptcp                                 │
└─────────────────────────────┬───────────────────────────────────┘
                              │ syscall / MPTCP netlink
┌─────────────────────────────▼───────────────────────────────────┐
│                   MPTCP SOCKET LAYER                            │
│               (net/mptcp/protocol.c + sockopt.c)                │
│                                                                 │
│  struct mptcp_sock  (msk)  ──  "meta socket"                   │
│  │                                                             │
│  │  snd_nxt / rcv_nxt  ── connection-level sequence numbers    │
│  │  subflow_list       ── all active subflows (TCP sockets)    │
│  │  pm                 ── path manager state                   │
│  │  scheduler          ── which subflow to send next skb on    │
│  │  token              ── MPTCP token for connection ID        │
│  │                                                             │
│  Socket ops: mptcp_stream_connect, mptcp_sendmsg,              │
│              mptcp_recvmsg, mptcp_close                        │
└──────────────────────┬──────────────────────┬───────────────────┘
                       │                      │
       ┌───────────────▼──────┐  ┌────────────▼──────────────────┐
       │  SUBFLOW LAYER       │  │  PATH MANAGER (PM)            │
       │  net/mptcp/subflow.c │  │  net/mptcp/pm_kernel.c        │
       │                      │  │  net/mptcp/pm_netlink.c       │
       │  mptcp_subflow_ctx   │  │  net/mptcp/pm_userspace.c     │
       │  (per TCP subflow)   │  │                               │
       │                      │  │  Decides when to add/remove   │
       │  Adds MPTCP option   │  │  subflows (additional IPs/    │
       │  to TCP SYN/ACK.     │  │  interfaces).                 │
       │  Handles MP_CAPABLE  │  │  Kernel PM: in-kernel policy  │
       │  MP_JOIN DSS options │  │  Userspace PM: via netlink    │
       └──────────┬───────────┘  └───────────────────────────────┘
                  │
       ┌──────────▼────────────────────────────────────────────┐
       │          TCP SUBFLOWS (ordinary TCP sockets)          │
       │                                                       │
       │  ssk1 (subflow 1, e.g., Wi-Fi eth0)                  │
       │  ssk2 (subflow 2, e.g., LTE wwan0)   ← added by PM   │
       │  ssk3 (subflow N …)                                   │
       │                                                       │
       │  Each is a full struct tcp_sock with MPTCP options    │
       └──────────┬────────────────────────────────────────────┘
                  │
┌─────────────────▼───────────────────────────────────────────────┐
│                  TCP / IPv4 / IPv6 / Network Interfaces         │
└─────────────────────────────────────────────────────────────────┘
```

---

## Layer-by-Layer Explanation

### 1. mptcp_sock — The Meta Socket

`struct mptcp_sock` wraps a `struct inet_connection_sock` and is the object
seen by userspace. Key responsibilities:

- Maintains the **connection-level data sequence space** (DSN — Data Sequence Numbers).
- Aggregates data arriving on any subflow into a single receive buffer.
- Distributes outgoing data across subflows according to the scheduler.
- Tracks subflow health; removes failed subflows and triggers adding new ones via PM.

### 2. mptcp_subflow_context — Per-subflow State

`struct mptcp_subflow_context` is embedded in each TCP subflow's socket via
`tcp_sk(ssk)->ulp_data`. It:

- Adds MPTCP options (`MP_CAPABLE`, `MP_JOIN`, `DSS`) to outgoing TCP segments.
- Strips and validates MPTCP options from incoming segments.
- Maps subflow-level sequence numbers ↔ data-level sequence numbers.

### 3. MPTCP Handshake (MP_CAPABLE / MP_JOIN)

```
Client (msk)              Subflow 1 (TCP)           Server
    │                           │                       │
    │  connect()                │                       │
    │ ─────────────────────────►│  SYN + MP_CAPABLE     │
    │                           │ ─────────────────────►│
    │                           │  SYN/ACK + MP_CAPABLE │
    │                           │◄──────────────────────│
    │                           │  ACK + MP_CAPABLE     │
    │                           │ ─────────────────────►│ (subflow 1 up)
    │                           │                       │
    │  PM adds second subflow   │                       │
    │                           │  SYN + MP_JOIN (token)│
    │                   ssk2 ──►│ ─────────────────────►│
    │                           │  SYN/ACK + MP_JOIN    │
    │                           │◄──────────────────────│
    │                           │  ACK + MP_JOIN        │
    │                           │ ─────────────────────►│ (subflow 2 up)
```

### 4. Data Sequence Signal (DSS)

Every MPTCP data segment carries a **DSS option** in the TCP header:

```
TCP segment:
  ┌─────────────────────────────────────────────────┐
  │ TCP header + DSS option:                        │
  │   Data Sequence Number (64-bit)                 │
  │   Subflow Sequence Number                       │
  │   Data Level Length                             │
  │   (optional) Data Checksum                      │
  ├─────────────────────────────────────────────────┤
  │  Application payload                            │
  └─────────────────────────────────────────────────┘
```

The receiver re-assembles payload in DSN order from all subflows.

### 5. Path Manager (PM)

The PM decides which IP addresses/interfaces to use for additional subflows:

| PM type | Location | Configuration |
|---|---|---|
| **Kernel PM** | `pm_kernel.c` / `pm_netlink.c` | `ip mptcp endpoint add …` |
| **Userspace PM** | `pm_userspace.c` | `mptcpd` daemon via netlink |

The kernel PM uses the `MPTCP_PM_*` netlink family for control.

### 6. Scheduler

`net/mptcp/sched.c` selects which subflow to send the next data chunk on.
Default: **first available**. Custom schedulers can be plugged in via
`mptcp_sched_ops`.

### 7. Fallback to Plain TCP

If the remote end does not support MPTCP (no `MP_CAPABLE` in SYN/ACK), the
kernel transparently falls back to a single TCP subflow — the application sees
no difference.

---

## Connection Setup Flow

```
Userspace                 mptcp_sock              subflow/TCP
    │                         │                       │
    │  connect(IPPROTO_MPTCP) │                       │
    │ ───────────────────────►│                       │
    │                         │  create subflow ssk1  │
    │                         │ ─────────────────────►│
    │                         │                       │ SYN+MP_CAPABLE
    │                         │                       │ ──► remote
    │                         │                       │◄── SYN/ACK+MP_CAPABLE
    │                         │◄──────────────────────│
    │                         │  mptcp_finish_connect()│
    │◄────────────────────────│ socket connected       │
    │                         │                       │
    │  send(data)             │                       │
    │ ───────────────────────►│  mptcp_sendmsg()       │
    │                         │  select subflow        │
    │                         │  add DSS option        │
    │                         │ ─────────────────────►│ TCP send
    │                         │                       │
    │                         │  PM: add ssk2 (LTE)   │
    │                         │ ─────────────────────►│ SYN+MP_JOIN ──► remote
    │                         │                       │ (second path up)
```

---

## Key Source Files

| File | Purpose |
|---|---|
| `net/mptcp/protocol.c` | mptcp_sock lifecycle, sendmsg, recvmsg, close |
| `net/mptcp/subflow.c` | TCP subflow creation, MPTCP option handling |
| `net/mptcp/options.c` | MPTCP TCP option encoding/decoding |
| `net/mptcp/pm_kernel.c` | In-kernel path manager |
| `net/mptcp/pm_netlink.c` | Netlink interface for PM control |
| `net/mptcp/pm_userspace.c` | Userspace PM delegation |
| `net/mptcp/sched.c` | Subflow scheduler framework |
| `net/mptcp/token.c` | MPTCP connection token management |
| `net/mptcp/ctrl.c` | sysctl knobs (`/proc/sys/net/mptcp/`) |
| `net/mptcp/diag.c` | `ss -M` socket diagnostics |
| `include/net/mptcp.h` | Public API |

---

## Observability

```bash
# Show MPTCP connections with subflow info
ss -MiH

# MPTCP endpoint management
ip mptcp endpoint show
ip mptcp endpoint add 192.168.1.10 dev eth0 subflow

# sysctl
sysctl net.mptcp.enabled          # 0=off 1=on
sysctl net.mptcp.checksum_enabled

# /proc
cat /proc/net/mptcp               # active MPTCP connections
```

---

## Analogy

MPTCP is like a **motorway with multiple lanes**:

- A regular TCP connection is a single-lane road — if it's blocked, you're stuck.
- An **MPTCP connection** is a multi-lane motorway — data flows over several roads
  (subflows) at once. If one lane closes (Wi-Fi drops), traffic seamlessly shifts
  to the other (LTE). The driver (application) just sees a single destination, not
  the lanes.
- The **path manager** is the GPS that decides which lanes to open or close.
- The **DSS option** is the lane marker ensuring packets are reassembled in
  the right order at the destination.

---

## References

- RFC 8684 — TCP Extensions for Multipath Operation
- `Documentation/networking/mptcp.rst`
- `include/net/mptcp.h`, `include/uapi/linux/mptcp.h`
- `net/mptcp/` — Full implementation
