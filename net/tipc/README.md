# Linux Kernel TIPC — Transparent Inter-Process Communication

## Overview

**TIPC** (Transparent Inter-Process Communication) is a cluster communication
protocol designed for **location-transparent messaging** between nodes in a
network cluster. It provides a **service addressing model** where applications
publish and subscribe to named services rather than connecting to specific
IP addresses and ports.

TIPC enables reliable, ordered message delivery with support for unicast,
multicast (group messaging), and connectionless/connection-oriented sockets.
It operates as a layer between transport (Ethernet/UDP bearers) and
applications, handling node discovery, link management, and automatic failover.

Source: `net/tipc/`, `include/uapi/linux/tipc.h`.

---

## Subsystem Stack

```
┌─────────────────────────────────────────────────────────────────┐
│                        USERSPACE                                │
│                                                                 │
│  tipc tool (iproute2)          Application sockets (AF_TIPC)   │
│   tipc bearer enable            socket(AF_TIPC, SOCK_RDM, 0)  │
│   tipc nametable show           bind({type, instance})         │
│   tipc node list                sendto() / recvfrom()          │
│   tipc link list                                                │
└───────────────────────────────┬─────────────────────────────────┘
                                │ Netlink / AF_TIPC socket
┌───────────────────────────────▼─────────────────────────────────┐
│                      TIPC SOCKET LAYER                         │
│                       (net/tipc/socket.c)                       │
│                                                                 │
│  Socket types: SOCK_STREAM, SOCK_SEQPACKET, SOCK_RDM, SOCK_DGRAM
│  Service addressing: {type, instance} → name table lookup      │
│  Connection / connectionless messaging                          │
│  Group messaging (multicast within a service group)             │
└───────────────────────────────┬─────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────┐
│                    TIPC NAME TABLE                              │
│                   (net/tipc/name_table.c)                       │
│                                                                 │
│  Service publications: {type, lower, upper, node, port}        │
│  Name distribution across cluster nodes                         │
│  Subscription mechanism for service availability events         │
│  Sequence-based lookup for load balancing                       │
└───────────────────────────────┬─────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────┐
│                       TIPC CORE                                │
│                                                                 │
│  ┌──────────────┐ ┌───────────────┐ ┌──────────────────────┐   │
│  │  node.c      │ │  link.c       │ │  msg.c               │   │
│  │  Peer node   │ │  Reliable     │ │  Message header      │   │
│  │  discovery,  │ │  point-to-    │ │  construction and    │   │
│  │  address     │ │  point links, │ │  parsing, routing    │   │
│  │  resolution  │ │  congestion   │ │  decisions           │   │
│  │              │ │  control,     │ │                      │   │
│  │              │ │  failover     │ │                      │   │
│  └──────────────┘ └───────────────┘ └──────────────────────┘   │
│  ┌──────────────┐ ┌───────────────┐ ┌──────────────────────┐   │
│  │  discover.c  │ │  bcast.c      │ │  group.c             │   │
│  │  Neighbor    │ │  Broadcast    │ │  Group messaging     │   │
│  │  discovery   │ │  link for     │ │  (multicast within   │   │
│  │  via DSC     │ │  cluster-wide │ │   service groups)    │   │
│  │  messages    │ │  delivery     │ │                      │   │
│  └──────────────┘ └───────────────┘ └──────────────────────┘   │
│  ┌──────────────┐ ┌───────────────┐                             │
│  │  crypto.c    │ │  netlink.c    │                             │
│  │  Encryption  │ │  Netlink      │                             │
│  │  (optional)  │ │  configuration│                             │
│  │  via AEAD    │ │  interface    │                             │
│  └──────────────┘ └───────────────┘                             │
└───────────────────────────────┬─────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────┐
│                      TIPC BEARER LAYER                         │
│                      (net/tipc/bearer.c)                       │
│                                                                 │
│  ┌──────────────────────┐  ┌────────────────────────────────┐  │
│  │  Ethernet bearer     │  │  UDP bearer                    │  │
│  │  (eth_media.c)       │  │  (udp_media.c)                 │  │
│  │  L2 direct transport │  │  IP/UDP encapsulation          │  │
│  │  over Ethernet NICs  │  │  (routable across L3)          │  │
│  └──────────────────────┘  └────────────────────────────────┘  │
└───────────────────────────────┬─────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────┐
│              PHYSICAL NETWORK (Ethernet / IP)                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Service Addressing Model

TIPC uses a **service-based addressing model** rather than traditional IP:port
addressing. Applications bind to and connect via **service addresses**:

```
{service_type, service_instance}        — single instance
{service_type, lower, upper}            — range of instances
```

**Name Table** maintains a cluster-wide registry:
- Applications **publish** services: bind a socket to a {type, instance}
- Applications **subscribe** to service availability events
- Name distribution protocol propagates publications across all nodes
- Lookup resolves service address → {node, port} for routing

**Addressing types in TIPC:**

| Address Type | Description |
|---|---|
| `TIPC_ADDR_NAMESEQ` | Service range {type, lower, upper} |
| `TIPC_ADDR_NAME` | Named service {type, instance, domain} |
| `TIPC_ADDR_ID` | Port identity {node, ref} |

---

## Key Data Structures

| Structure | File | Purpose |
|---|---|---|
| `struct tipc_node` | `node.c` | Peer node in the cluster — tracks state, links, capabilities |
| `struct tipc_link` | `link.c` | Reliable point-to-point link between two nodes — sequence numbers, retransmit queue, congestion window |
| `struct tipc_bearer` | `bearer.h` | Transport medium (Ethernet or UDP) — send/receive interface |
| `struct tipc_msg` | `msg.h` | TIPC message header — routing info, service address, sequence numbers |
| `struct tipc_sock` | `socket.c` | TIPC socket (wraps struct sock) — connected/connectionless state |
| `struct publication` | `name_table.c` | Service publication entry in the name table |
| `struct tipc_subscription` | `subscr.c` | Service availability subscription |
| `struct tipc_net` | `core.h` | Per-network-namespace TIPC state |
| `struct tipc_group` | `group.c` | Group messaging state for multicast within a service group |

---

## Key Functions

| Function | File | Purpose |
|---|---|---|
| `tipc_rcv()` | `node.c` | Main receive entry — dispatches incoming TIPC messages to links/sockets |
| `tipc_send_stream()` | `socket.c` | Send data on a TIPC stream socket (SOCK_STREAM) |
| `tipc_sendmsg()` | `socket.c` | Send a message on a TIPC socket (connectionless) |
| `tipc_node_link_up()` | `node.c` | Handle a link coming up — update node state, trigger failover if needed |
| `tipc_node_link_down()` | `node.c` | Handle a link going down — trigger failover to standby link |
| `tipc_node_create()` | `node.c` | Create a new peer node entry when discovered |
| `tipc_link_build_proto_msg()` | `link.c` | Build link protocol messages (STATE_MSG, RESET_MSG, ACTIVATE_MSG) |
| `tipc_sk_create()` | `socket.c` | Create a new TIPC socket (AF_TIPC) |
| `tipc_nametbl_publish()` | `name_table.c` | Publish a service in the name table |
| `tipc_bearer_send()` | `bearer.c` | Send a message via a bearer (Ethernet/UDP) |
| `tipc_disc_rcv()` | `discover.c` | Process a neighbor discovery message |

---

## Key Source Files

| File | Purpose |
|---|---|
| `net/tipc/core.c` | Module init, per-netns setup |
| `net/tipc/node.c` | Peer node management, message receive dispatch |
| `net/tipc/link.c` | Reliable link protocol — retransmit, flow control |
| `net/tipc/bearer.c` | Bearer (transport medium) management |
| `net/tipc/socket.c` | AF_TIPC socket operations |
| `net/tipc/name_table.c` | Service name publication and lookup |
| `net/tipc/name_distr.c` | Cluster-wide name distribution protocol |
| `net/tipc/subscr.c` | Service subscription / topology events |
| `net/tipc/discover.c` | Neighbor discovery protocol |
| `net/tipc/msg.c` / `msg.h` | Message header format and helpers |
| `net/tipc/bcast.c` | Broadcast link for cluster-wide delivery |
| `net/tipc/group.c` | Group messaging (application multicast) |
| `net/tipc/crypto.c` | TIPC encryption (AEAD-based) |
| `net/tipc/netlink.c` | Netlink configuration interface |
| `net/tipc/udp_media.c` | UDP bearer transport |
| `net/tipc/eth_media.c` | Ethernet bearer transport |
| `net/tipc/trace.h` | Tracepoint definitions |
| `include/uapi/linux/tipc.h` | UAPI: socket address, message types |
| `include/uapi/linux/tipc_config.h` | UAPI: legacy configuration |
| `include/uapi/linux/tipc_netlink.h` | UAPI: netlink attributes |

---

## Tracing and Debugging

### Tracepoints

TIPC defines tracepoints in `net/tipc/trace.h`:

```bash
# List available TIPC tracepoints
grep tipc /sys/kernel/debug/tracing/available_events

# Example tracepoints:
#   tipc:tipc_link_timeout
#   tipc:tipc_link_reset
#   tipc:tipc_sk_sendmsg
#   tipc:tipc_sk_poll
```

### bpftrace Examples

```bash
# Trace incoming TIPC messages
bpftrace -e 'kprobe:tipc_rcv { printf("tipc_rcv skb=%p bearer=%p\n", arg0, arg1); }'

# Trace TIPC socket creation
bpftrace -e 'kprobe:tipc_sk_create { printf("tipc_sk_create net=%p sock=%p\n", arg0, arg1); }'

# Trace link state changes
bpftrace -e 'kprobe:tipc_node_link_up {
    printf("LINK UP node=%p bearer_id=%d\n", arg0, arg1);
}'

# Trace link protocol messages
bpftrace -e 'kprobe:tipc_link_build_proto_msg {
    printf("LINK PROTO link=%p\n", arg0);
}'
```

### tipc CLI Tool

```bash
# Bearer management
tipc bearer enable media eth device eth0
tipc bearer list

# Node information
tipc node list
tipc node set addr 1.1.1

# Link management
tipc link list
tipc link show

# Name table
tipc nametable show

# Monitoring
tipc link monitor list
```

### Module Parameters

```bash
# Check if TIPC module is loaded
lsmod | grep tipc

# Load TIPC module
modprobe tipc

# Check TIPC symbols
grep ' tipc_' /proc/kallsyms | head
```

---

## Quick Reference

```bash
# Enable Ethernet bearer
tipc bearer enable media eth device eth0

# Set node address (legacy)
tipc node set addr 1.1.1

# Show cluster topology
tipc node list
tipc link list

# Show published services
tipc nametable show

# Show socket info
tipc socket list

# Monitor link events
tipc link monitor summary
```

---

## Analogy

TIPC is like a **company-wide intercom system**:

- **Nodes** are buildings in the campus — each one has a unique address.
- **Bearers** are the physical phone lines connecting buildings (Ethernet
  cables or VoIP/UDP tunnels).
- **Links** are the active phone connections — they handle reliable delivery,
  retransmission, and failover (if one line breaks, switch to another).
- **Service addresses** are extension numbers by department — you call
  "Accounting, desk 42" rather than "building 3, floor 2, room 207."
- **Name table** is the company directory — it maps department names to
  physical locations, and everyone gets notified when someone joins or leaves.
- **Group messaging** is the department-wide conference call — one message
  reaches all members of a service group.

---

## References

- `net/tipc/` — Full implementation
- `include/uapi/linux/tipc.h` — Socket API
- `include/uapi/linux/tipc_netlink.h` — Netlink interface
- `Documentation/networking/tipc.rst` — Kernel documentation
- [TIPC project site](http://tipc.io/) — Protocol specification and guides
