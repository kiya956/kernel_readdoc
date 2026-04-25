# NET/ROM — Amateur Radio Network Transport Protocol

## Overview

The NET/ROM subsystem implements the NET/ROM amateur radio protocol in the Linux
kernel. NET/ROM provides a layer 3/4 (network/transport) protocol that runs over
AX.25 data links, enabling multi-hop packet routing across amateur radio networks.

Key features:

- **Multi-hop routing** — Packets traverse multiple nodes to reach destination
- **Virtual circuits** — Connection-oriented reliable transport
- **Node discovery** — Automatic neighbor and routing table updates
- **AF_NETROM sockets** — BSD socket interface for applications

NET/ROM was designed for the amateur packet radio community and is still used by
ham radio operators worldwide. Each node has a callsign-based address.

## Kernel Source

- **Directory:** `net/netrom/`
- **Headers:** `include/net/netrom.h`
- **Config:** `CONFIG_NETROM`

## Architecture

```
┌─────────────────────────────────────────────┐
│         User Space Applications             │
│      (node, call, axlisten, BBS)           │
├─────────────────────────────────────────────┤
│          AF_NETROM Socket Layer             │
│     nr_sendmsg / nr_recvmsg                │
├─────────────────────────────────────────────┤
│          NET/ROM Transport                  │
│   ┌────────────┬─────────────────────┐      │
│   │ Connection │    Routing          │      │
│   │ Management │  nr_node table      │      │
│   │ nr_sock    │  nr_neigh table     │      │
│   └────────────┴─────────────────────┘      │
├─────────────────────────────────────────────┤
│            AX.25 Data Link                  │
│     ax25_send_frame / ax25_rcv             │
├─────────────────────────────────────────────┤
│         Radio Hardware (TNC/Modem)          │
│     KISS, 6PACK, SCC, baycom               │
└─────────────────────────────────────────────┘
```

## Packet Flow

```
 SEND PATH                              RECEIVE PATH
 ─────────                              ────────────

 User application                       AX.25 delivers frame
     │                                       │
     ▼                                       ▼
 nr_sendmsg()                           nr_rcv()
     │                                       │
     ▼                                       ▼
 Build NET/ROM header                   Parse NET/ROM header
 (src, dst, circuit ID)                 (check destination)
     │                                       │
     ▼                                  ┌────┴─────┐
 nr_output()                           │          │
     │                              For us?    Forward?
     ▼                                  │          │
 Fragment if needed                     ▼          ▼
     │                            nr_process   nr_route
     ▼                            _rx_frame    _frame
 AX.25 send to                         │          │
 next-hop neighbor                      ▼          ▼
     │                            Socket recv  Next-hop TX
     ▼
 Radio TX
```

## Key Structures

| Structure | File | Purpose |
|-----------|------|---------|
| `struct nr_sock` | `include/net/netrom.h` | Per-connection socket state |
| `struct nr_node` | `include/net/netrom.h` | Routing table entry — destination node |
| `struct nr_neigh` | `include/net/netrom.h` | Neighbor entry — adjacent AX.25 station |
| `struct nr_route` | `include/net/netrom.h` | Route quality and neighbor association |

## Key Functions

| Function | File | Purpose |
|----------|------|---------|
| `nr_rcv()` | `net/netrom/nr_in.c` | Main receive — dispatch incoming frames |
| `nr_sendmsg()` | `net/netrom/af_netrom.c` | Socket sendmsg for AF_NETROM |
| `nr_recvmsg()` | `net/netrom/af_netrom.c` | Socket recvmsg for AF_NETROM |
| `nr_output()` | `net/netrom/nr_out.c` | Fragment and queue for transmission |
| `nr_route_frame()` | `net/netrom/nr_route.c` | Forward frame to next hop |
| `nr_add_node()` | `net/netrom/nr_route.c` | Add/update routing table entry |

## Analogy

NET/ROM is like a **ham radio postal service**. Each radio operator (node) has a
callsign address. When you want to send a message to a distant operator, NET/ROM
finds a chain of relay stations to pass the message along — like handing a letter
to a series of couriers. Each courier (neighbor) knows the next courier in the
chain. The routing table is like a phone book that says "to reach station XYZ,
hand the message to station ABC, who is 2 hops closer."
