# Sun RPC Subsystem (`net/sunrpc`)

## Overview

Sun RPC (ONC RPC) is the kernel's Remote Procedure Call framework, primarily
serving as the transport for NFS (Network File System). It provides both client
(`rpc_clnt`) and server (`svc`) infrastructure, supporting TCP and UDP
transports, portmapper/rpcbind service registration, XDR (External Data
Representation) encoding/decoding, and GSSAPI-based authentication (RPCSEC_GSS).

The subsystem manages the full lifecycle of RPC calls — from marshalling
arguments, sending requests, handling retransmissions, to unmarshalling replies.
It includes an asynchronous task scheduler (`rpc_task`) that allows multiple
outstanding RPC calls with priority-based scheduling.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                NFS Client / NFS Server                  │
│            (nfs_read, nfs_write, nfsd_dispatch)         │
├─────────────────────────────────────────────────────────┤
│                    Sun RPC Layer                         │
│  ┌──────────────────────┐  ┌──────────────────────────┐ │
│  │   Client (rpc_clnt)  │  │   Server (svc_serv)      │ │
│  │  ┌────────────────┐  │  │  ┌────────────────────┐  │ │
│  │  │   rpc_task     │  │  │  │   svc_rqst         │  │ │
│  │  │  (async call)  │  │  │  │  (request handler) │  │ │
│  │  └───────┬────────┘  │  │  └───────┬────────────┘  │ │
│  │          │            │  │          │               │ │
│  │  ┌───────▼────────┐  │  │  ┌───────▼────────────┐  │ │
│  │  │  XDR encode    │  │  │  │  XDR decode        │  │ │
│  │  │  /decode       │  │  │  │  /encode           │  │ │
│  │  └───────┬────────┘  │  │  └───────┬────────────┘  │ │
│  └──────────┼───────────┘  └──────────┼───────────────┘ │
│             │                         │                 │
│  ┌──────────▼─────────────────────────▼───────────────┐ │
│  │               rpc_xprt (transport)                 │ │
│  │    ┌──────────┐  ┌──────────┐  ┌──────────────┐   │ │
│  │    │   TCP    │  │   UDP    │  │   RDMA (xprtrdma)│ │
│  │    └──────────┘  └──────────┘  └──────────────┘   │ │
│  └────────────────────────────────────────────────────┘ │
├─────────────────────────────────────────────────────────┤
│              Auth (RPCSEC_GSS / AUTH_UNIX)               │
├─────────────────────────────────────────────────────────┤
│                   Socket / RDMA Layer                    │
└─────────────────────────────────────────────────────────┘
```

## RPC Call Workflow (Client)

```
  NFS Client                           NFS Server
      │                                    │
      │  rpc_call_sync() / rpc_run_task()  │
      │         │                          │
      │    ┌────▼────────────┐             │
      │    │ XDR encode args │             │
      │    └────┬────────────┘             │
      │         │                          │
      │    ┌────▼────────────┐             │
      │    │ xprt_transmit() │             │
      │    └────┬────────────┘             │
      │         │                          │
      │──── RPC Request ─────────────────►│
      │     (XID, program, proc)           │
      │                                    │
      │◄──── RPC Reply ───────────────────│
      │      (XID, status, data)           │
      │         │                          │
      │    ┌────▼────────────┐             │
      │    │ XDR decode reply│             │
      │    └────┬────────────┘             │
      │         │                          │
      │    [rpc_task completes]            │
      │                                    │

  Async task states:
    TASK_SETUP ──► TASK_CALL_TRANSMIT ──► TASK_CALL_RECEIVE ──► TASK_COMPLETE
```

## Key Structures

| Structure            | Description                                              |
|----------------------|----------------------------------------------------------|
| `struct rpc_clnt`    | RPC client handle — program, version, transport binding  |
| `struct rpc_xprt`    | RPC transport — TCP/UDP/RDMA connection state             |
| `struct rpc_task`    | Async RPC call — state machine, callback chain            |
| `struct svc_rqst`    | Server request — incoming call being processed            |
| `struct svc_serv`    | Server instance — registered programs, thread pool        |
| `struct rpc_message` | RPC message — procedure, args, reply, credentials         |
| `struct xdr_stream`  | XDR encode/decode stream cursor                           |

## Key Functions

| Function               | Description                                       |
|------------------------|---------------------------------------------------|
| `rpc_call_sync()`      | Synchronous RPC call — blocks until reply          |
| `rpc_run_task()`       | Start an async RPC task                            |
| `svc_process()`        | Server: process one incoming RPC request           |
| `xprt_transmit()`      | Transmit RPC request over transport                |
| `xprt_connect()`       | Establish transport connection                     |
| `rpc_create()`         | Create a new RPC client                            |
| `svc_recv()`           | Server: wait for and receive an RPC request        |
| `rpcauth_wrap_req()`   | Wrap request with auth credentials                 |

## Analogy

Sun RPC is like a **corporate call center** for NFS. The `rpc_clnt` is a
customer with a question. The `rpc_task` is the ticket tracking the call.
The `rpc_xprt` is the phone line (TCP/UDP/RDMA). XDR is the agreed-upon
language both sides speak. The `svc_serv` is the call center with a pool
of agents (`svc_rqst`), each handling one call at a time. RPCSEC_GSS is
the caller ID verification — ensuring requests are authentic before the
agent processes them.

## Source Files

| File                       | Purpose                                |
|----------------------------|----------------------------------------|
| `net/sunrpc/clnt.c`        | RPC client core                        |
| `net/sunrpc/svc.c`         | RPC server core                        |
| `net/sunrpc/xprt.c`        | Transport abstraction                  |
| `net/sunrpc/xprtsock.c`    | TCP/UDP transport implementation       |
| `net/sunrpc/sched.c`       | RPC task scheduler                     |
| `net/sunrpc/auth_gss/`     | GSSAPI authentication                  |
| `net/sunrpc/xdr.c`         | XDR encode/decode routines             |
