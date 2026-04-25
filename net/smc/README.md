# Shared Memory Communications Subsystem (`net/smc`)

## Overview

SMC (Shared Memory Communications) is a protocol that transparently replaces
TCP connections with RDMA-based (SMC-R) or shared-memory-based (SMC-D) data
transfer. Originally developed by IBM for z-series mainframes, it operates at
the socket layer so applications using TCP sockets can benefit from RDMA speeds
without code changes.

During the TCP handshake, SMC peers negotiate whether to upgrade to SMC-R
(over RDMA NICs / RoCE) or SMC-D (over ISM — Internal Shared Memory devices).
If negotiation fails, the connection falls back transparently to plain TCP.

## Architecture

```
┌─────────────────────────────────────────────────────┐
│               Application (TCP socket API)          │
├─────────────────────────────────────────────────────┤
│                  SMC Socket Layer                    │
│  ┌────────────────────────────────────────────────┐ │
│  │   smc_sock  (wraps internal TCP + SMC conn)    │ │
│  └──────────┬─────────────────┬───────────────────┘ │
│             │                 │                      │
│    ┌────────▼────────┐ ┌─────▼──────────┐           │
│    │   SMC-R Path    │ │   SMC-D Path   │           │
│    │  (RDMA / RoCE)  │ │  (ISM device)  │           │
│    └────────┬────────┘ └─────┬──────────┘           │
│             │                │                      │
│    ┌────────▼────────┐ ┌─────▼──────────┐           │
│    │ smc_link_group  │ │ smc_link_group │           │
│    │   smc_link      │ │   (DMB-based)  │           │
│    │   (QP / MR)     │ │                │           │
│    └────────┬────────┘ └─────┬──────────┘           │
├─────────────┼────────────────┼──────────────────────┤
│        IB Verbs API      ISM Device Driver          │
├─────────────┼────────────────┼──────────────────────┤
│         RDMA NIC           ISM Hardware              │
└─────────────┴────────────────┴──────────────────────┘
```

## Connection Lifecycle Workflow

```
  Client                              Server
    │                                   │
    │──── TCP SYN + CLC Proposal ──────►│
    │                                   │
    │◄─── TCP SYN-ACK + CLC Accept ─────│
    │                                   │
    │──── CLC Confirm ─────────────────►│
    │     (RDMA params / ISM GID)       │
    │                                   │
    │     [SMC connection established]   │
    │                                   │
    │◄══════ RDMA WRITE / ISM ══════════►│
    │     (zero-copy data transfer)      │
    │                                   │
    │     [smc_sendmsg / smc_recvmsg]   │
    │                                   │

  Fallback path (if CLC negotiation fails):
    Client ◄──── plain TCP ────► Server
```

## Key Structures

| Structure              | Description                                              |
|------------------------|----------------------------------------------------------|
| `struct smc_sock`      | SMC socket — wraps TCP clcsock + SMC connection           |
| `struct smc_connection`| Per-connection state — sndbuf, RMB, cursors               |
| `struct smc_link_group`| Group of links sharing RDMA resources                     |
| `struct smc_link`      | Single RDMA link — QP, MR, and link state                 |
| `struct smc_buf_desc`  | Shared buffer descriptor — RMB or sndbuf                  |
| `struct smc_clc_msg_proposal` | CLC negotiation proposal message                   |

## Key Functions

| Function             | Description                                        |
|----------------------|----------------------------------------------------|
| `smc_sendmsg()`      | Send data via SMC (RDMA write or ISM)              |
| `smc_recvmsg()`      | Receive data from remote memory buffer             |
| `smc_connect()`      | Initiate SMC connection with CLC negotiation       |
| `smc_accept()`       | Accept incoming SMC connection                     |
| `smc_close()`        | Close SMC connection and release resources          |
| `smc_clc_send_proposal()` | Send CLC proposal during handshake            |
| `smc_llc_do_confirm_rkey()` | Confirm RKEY for RDMA memory registration   |

## Analogy

Imagine TCP as sending letters through the postal service — reliable but each
message passes through many hands. SMC is like **running a private pneumatic
tube** between two offices in the same building. The initial handshake (CLC)
checks if both offices have tube connections. If they do, data shoots through
the tube (RDMA/ISM) at near-zero latency. If the tube isn't available, they
fall back to regular mail (TCP) transparently — the sender never notices.

## Source Files

| File                   | Purpose                              |
|------------------------|--------------------------------------|
| `net/smc/smc.c`        | Core socket operations               |
| `net/smc/smc_clc.c`    | CLC handshake protocol               |
| `net/smc/smc_core.c`   | Link group and connection management |
| `net/smc/smc_ib.c`     | InfiniBand / RDMA integration        |
| `net/smc/smc_ism.c`    | ISM device integration (SMC-D)       |
| `net/smc/smc_tx.c`     | Transmit path                        |
| `net/smc/smc_rx.c`     | Receive path                         |
