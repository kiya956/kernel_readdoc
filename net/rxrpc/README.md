# RxRPC Protocol Subsystem (`net/rxrpc`)

## Overview

RxRPC is a connection-oriented RPC (Remote Procedure Call) protocol transported
over UDP. It is the native transport for AFS (Andrew File System) and provides
reliable, multiplexed RPC channels with built-in security negotiation. Unlike
typical RPC frameworks that layer on TCP, RxRPC manages its own congestion
control, retransmission, and connection multiplexing directly on UDP datagrams.

The Linux kernel implementation provides both a userspace socket interface
(`AF_RXRPC`) and an in-kernel API used by the kAFS filesystem client and
server (`kafs`/`kAFS`).

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                  Userspace / kAFS                   │
│         (AF_RXRPC sockets / kernel API)             │
├─────────────────────────────────────────────────────┤
│                  RxRPC Call Layer                    │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐    │
│  │  rxrpc_call│  │  rxrpc_call│  │  rxrpc_call│    │
│  │  (RPC #1)  │  │  (RPC #2)  │  │  (RPC #3)  │    │
│  └─────┬──────┘  └─────┬──────┘  └─────┬──────┘    │
│        └───────────┬────┴──────────────┘            │
├────────────────────┼────────────────────────────────┤
│          rxrpc_connection (mux)                      │
│    ┌───────────────┼───────────────┐                │
│    │  Security Negotiation (rxkad) │                │
│    └───────────────┼───────────────┘                │
├────────────────────┼────────────────────────────────┤
│              rxrpc_peer                              │
│         (remote endpoint tracking)                   │
├────────────────────┼────────────────────────────────┤
│              rxrpc_local                             │
│          (local UDP socket binding)                  │
├────────────────────┼────────────────────────────────┤
│                 UDP Transport                        │
│              (sk_buff / sendmsg)                     │
└────────────────────┼────────────────────────────────┘
                     │
              ───────┴──────── Network
```

## Call Lifecycle Workflow

```
  Client                                 Server
    │                                      │
    │──── DATA (request) ────────────────►│
    │     [call_id, seq=0..N]              │
    │                                      │
    │◄─── ACK ─────────────────────────────│
    │                                      │
    │◄─── DATA (reply) ────────────────────│
    │     [call_id, seq=0..M]              │
    │                                      │
    │──── ACK ────────────────────────────►│
    │                                      │
    │     [call completes, channel freed]   │
    │                                      │

  State machine per call:
    CLIENT_SEND_REQUEST ──► CLIENT_AWAIT_REPLY ──► CLIENT_RECV_REPLY ──► COMPLETE
    SERVER_AWAIT_REQUEST ──► SERVER_RECV_REQUEST ──► SERVER_SEND_REPLY ──► COMPLETE
```

## Key Structures

| Structure              | Description                                                |
|------------------------|------------------------------------------------------------|
| `struct rxrpc_call`    | Single RPC call — tracks state, sequence numbers, buffers  |
| `struct rxrpc_connection` | Multiplexed connection — up to 4 concurrent calls       |
| `struct rxrpc_peer`    | Remote endpoint — RTT estimation, MTU, congestion state    |
| `struct rxrpc_local`   | Local UDP socket binding — shared across connections       |
| `struct rxrpc_sock`    | AF_RXRPC socket — userspace interface                      |
| `struct rxrpc_skb_priv`| Per-skb metadata — sequence number, flags                  |

## Key Functions

| Function                    | Description                                         |
|-----------------------------|-----------------------------------------------------|
| `rxrpc_kernel_send_data()`  | Kernel API: send data on an RPC call                |
| `rxrpc_recvmsg()`           | Receive RPC data/notifications on AF_RXRPC socket   |
| `rxrpc_sendmsg()`           | Send RPC data via AF_RXRPC socket                   |
| `rxrpc_new_client_call()`   | Allocate and initiate a new outgoing RPC call       |
| `rxrpc_input_packet()`      | Process an incoming UDP packet                      |
| `rxrpc_send_abort_packet()` | Abort a call with an error code                     |
| `rxrpc_propose_ack()`       | Schedule an ACK for transmission                    |
| `rxrpc_rotate_tx_window()`  | Advance transmit window after ACK                   |

## Analogy

Think of RxRPC like a **phone switchboard with numbered call lines**. The
`rxrpc_local` is the switchboard (UDP socket). Each `rxrpc_peer` is a remote
office you call. An `rxrpc_connection` is a trunk line to that office carrying
up to 4 simultaneous calls. Each `rxrpc_call` is one conversation — strictly
request-then-reply — and the call ID ensures replies go to the right caller
even when multiplexed on the same trunk.

## Source Files

| File                  | Purpose                              |
|-----------------------|--------------------------------------|
| `net/rxrpc/call_object.c` | Call lifecycle management         |
| `net/rxrpc/conn_client.c` | Client connection management     |
| `net/rxrpc/input.c`       | Incoming packet processing       |
| `net/rxrpc/output.c`      | Outgoing packet transmission     |
| `net/rxrpc/sendmsg.c`     | sendmsg() implementation         |
| `net/rxrpc/recvmsg.c`     | recvmsg() implementation         |
| `net/rxrpc/peer_object.c` | Peer tracking and RTT            |
