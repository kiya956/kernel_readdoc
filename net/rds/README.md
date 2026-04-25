# RDS — Reliable Datagram Sockets

## Overview

The RDS (Reliable Datagram Sockets) subsystem implements Oracle's high-performance,
reliable, ordered datagram protocol in the Linux kernel. RDS is designed for
data-center environments where applications need fast, reliable messaging with
optional RDMA (Remote Direct Memory Access) support.

Key features:

- **Reliable delivery** — All messages delivered exactly once, in order
- **Zero-copy RDMA** — Direct memory-to-memory transfers (via InfiniBand/RoCE)
- **Congestion control** — Per-destination flow control
- **AF_RDS sockets** — Standard socket interface
- **Multiple transports** — TCP fallback, InfiniBand, and RDMA

RDS is primarily used in Oracle Database RAC (Real Application Clusters) and
other HPC/cloud workloads requiring low-latency, reliable messaging.

## Kernel Source

- **Directory:** `net/rds/`
- **Headers:** `include/net/rds.h`, `include/uapi/linux/rds.h`
- **Config:** `CONFIG_RDS`, `CONFIG_RDS_TCP`, `CONFIG_RDS_RDMA`

## Architecture

```
┌─────────────────────────────────────────────┐
│         User Space (Oracle RAC, etc.)       │
│            AF_RDS sockets                   │
├─────────────────────────────────────────────┤
│           RDS Socket Layer                  │
│     rds_sendmsg / rds_recvmsg              │
├─────────────────────────────────────────────┤
│           RDS Core Engine                   │
│  ┌────────────────────────────────────┐     │
│  │  Connection Manager                │     │
│  │  rds_connection (per src/dst pair) │     │
│  ├────────────────────────────────────┤     │
│  │  Message Queue                     │     │
│  │  rds_message, send/retransmit      │     │
│  ├────────────────────────────────────┤     │
│  │  Congestion Control                │     │
│  │  Per-destination congestion map    │     │
│  ├────────────────────────────────────┤     │
│  │  RDMA Operations (optional)        │     │
│  │  MR registration, RDMA read/write  │     │
│  └────────────────────────────────────┘     │
├─────────────────────────────────────────────┤
│          RDS Transport Layer                │
│  ┌──────────┬──────────┬────────────┐       │
│  │   TCP    │   IB     │   RDMA     │       │
│  │ fallback │InfiniBand│ zero-copy  │       │
│  └──────────┴──────────┴────────────┘       │
├─────────────────────────────────────────────┤
│      Network / RDMA Hardware                │
└─────────────────────────────────────────────┘
```

## Packet Flow

```
 SEND PATH                              RECEIVE PATH
 ─────────                              ────────────

 Application sendmsg()                  Transport receives data
     │                                       │
     ▼                                       ▼
 rds_sendmsg()                          rds_recv_incoming()
     │                                       │
     ▼                                       ▼
 Allocate rds_message                   Find rds_connection
 Copy data from user                    for (src_ip, dst_ip)
     │                                       │
     ▼                                       ▼
 Find/create rds_connection             Queue rds_message on
 for (src_ip, dst_ip)                   connection recv list
     │                                       │
     ▼                                       ▼
 Queue message on                       Notify waiting socket
 connection send list                   rds_wake_sk_sleep()
     │                                       │
     ▼                                       ▼
 rds_send_xmit()                        rds_recvmsg()
     │                                       │
     ▼                                       ▼
 transport->xmit()                      Copy data to user
 (TCP send / IB post)                   Acknowledge receipt
     │
     ▼
 Wait for ACK, retransmit if needed
```

## Key Structures

| Structure | File | Purpose |
|-----------|------|---------|
| `struct rds_connection` | `include/net/rds.h` | Per (src, dst) IP pair connection state |
| `struct rds_message` | `include/net/rds.h` | Queued message with headers and data |
| `struct rds_transport` | `include/net/rds.h` | Transport ops (TCP, IB, RDMA) |
| `struct rds_sock` | `include/net/rds.h` | Per-socket RDS state |
| `struct rds_incoming` | `include/net/rds.h` | Incoming message wrapper |

## Key Functions

| Function | File | Purpose |
|----------|------|---------|
| `rds_sendmsg()` | `net/rds/send.c` | Socket sendmsg — queue message for delivery |
| `rds_recvmsg()` | `net/rds/recv.c` | Socket recvmsg — dequeue received message |
| `rds_recv_incoming()` | `net/rds/recv.c` | Process incoming message from transport |
| `rds_send_xmit()` | `net/rds/send.c` | Transmit queued messages via transport |
| `rds_conn_create()` | `net/rds/connection.c` | Create/find connection for (src, dst) pair |
| `rds_connect_complete()` | `net/rds/connection.c` | Connection established callback |

## Analogy

RDS is like a **premium courier service between office buildings**. Each building
(server) has a street address (IP). When office A sends a package to office B, the
courier (RDS connection) guarantees delivery in order, with tracking (ACKs) and
re-delivery if lost. For really heavy loads, there's a freight elevator (RDMA) that
moves entire filing cabinets directly between offices without going through the
mailroom. The courier company offers different vehicle types (transports): regular
truck (TCP) or express van (InfiniBand).
