# Netlink — Kernel-Userspace IPC Subsystem

## Overview

**Netlink** is a Linux kernel IPC mechanism that provides **bidirectional
communication between kernel and userspace** via `AF_NETLINK` sockets.  Unlike
`ioctl()`, Netlink supports asynchronous, multicast, multipart messages and is
the primary transport for:

- **NETLINK_ROUTE** — routing tables, network interfaces, addresses (iproute2)
- **NETLINK_GENERIC** — extensible generic netlink families (genetlink)
- **NETLINK_NETFILTER** — nftables / iptables configuration
- **NETLINK_KOBJECT_UEVENT** — udev device hotplug events
- **NETLINK_AUDIT** — security audit subsystem
- **NETLINK_XFRM** — IPsec SA/policy management
- **NETLINK_CONNECTOR** — kernel connector notifications
- **NETLINK_SOCK_DIAG** — socket diagnostics

Userspace creates an `AF_NETLINK` socket, binds to a multicast group, and
exchanges structured messages with the kernel.  Each message begins with a
`struct nlmsghdr` header followed by protocol-specific payload and nested
TLV attributes (`struct nlattr`).

Source: `net/netlink/`, `include/net/netlink.h`, `include/uapi/linux/netlink.h`,
`include/net/genetlink.h`.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                          USERSPACE                                    │
│                                                                      │
│  iproute2 (ip, ss, bridge)    libnl / libmnl    wpa_supplicant       │
│  NetworkManager               systemd-networkd   ethtool (nl)        │
│                                                                      │
│     socket(AF_NETLINK, SOCK_DGRAM, NETLINK_ROUTE)                    │
│     bind(fd, {.nl_family=AF_NETLINK, .nl_groups=RTMGRP_LINK})       │
│     sendmsg(fd, nlmsghdr + payload)                                  │
│     recvmsg(fd, nlmsghdr + payload)                                  │
└────────────────────────────────┬─────────────────────────────────────┘
                                 │ AF_NETLINK socket
                                 │ (net/netlink/af_netlink.c)
┌────────────────────────────────▼─────────────────────────────────────┐
│              NETLINK SOCKET LAYER  (af_netlink.c)                    │
│                                                                      │
│  netlink_create()    — create netlink socket (PF_NETLINK)            │
│  netlink_bind()      — bind socket to groups                         │
│  netlink_sendmsg()   — userspace → kernel message dispatch           │
│  netlink_recvmsg()   — kernel → userspace message delivery           │
│  netlink_unicast()   — send to specific port/pid                     │
│  netlink_broadcast() — send to multicast group                       │
│                                                                      │
│  struct netlink_sock — per-socket state (portid, groups, cb)         │
│  struct netlink_table — per-protocol hash table of sockets           │
└──────┬──────────────┬──────────────┬──────────────┬──────────────────┘
       │              │              │              │
       ▼              ▼              ▼              ▼
  ┌─────────┐   ┌──────────┐  ┌──────────┐  ┌───────────────┐
  │NETLINK  │   │NETLINK   │  │NETLINK   │  │NETLINK        │
  │_ROUTE   │   │_GENERIC  │  │_NETFILTER│  │_KOBJECT_UEVENT│
  │         │   │(genetlink│  │          │  │               │
  │rtnetlink│   │ families)│  │nfnetlink │  │uevent         │
  │net/core/│   │net/netlink│ │net/      │  │lib/kobject_   │
  │rtnetlink│   │genetlink.c│ │netfilter/│  │uevent.c       │
  │.c       │   │          │  │nfnetlink │  │               │
  └─────────┘   └──────────┘  └──────────┘  └───────────────┘
```

---

## Message Format

```
                        Netlink Message (skb->data)
┌───────────────────────────────────────────────────────────────┐
│                     struct nlmsghdr (16 bytes)                 │
│  ┌────────────┬──────────┬──────────┬──────────┐             │
│  │ nlmsg_len  │nlmsg_type│nlmsg_flags│nlmsg_seq│             │
│  │ (32 bits)  │(16 bits) │(16 bits)  │(32 bits)│             │
│  └────────────┴──────────┴──────────┴──────────┘             │
│  │ nlmsg_pid (32 bits)                         │             │
│  └─────────────────────────────────────────────┘             │
├───────────────────────────────────────────────────────────────┤
│                     PAYLOAD (protocol-specific)               │
│                                                               │
│  ┌─────────────────────────────────────────────┐             │
│  │ Protocol header (e.g. struct ifinfomsg,     │             │
│  │ struct rtmsg, struct genlmsghdr)            │             │
│  └─────────────────────────────────────────────┘             │
│  ┌─────────────────────────────────────────────┐             │
│  │ Nested TLV attributes (struct nlattr)       │             │
│  │  ┌──────┬──────┬──────────────────┐         │             │
│  │  │nla_len│nla_type│     data      │         │             │
│  │  └──────┴──────┴──────────────────┘         │             │
│  │  ┌──────┬──────┬──────────────────┐         │             │
│  │  │nla_len│nla_type│     data      │         │             │
│  │  └──────┴──────┴──────────────────┘         │             │
│  └─────────────────────────────────────────────┘             │
└───────────────────────────────────────────────────────────────┘

Request / Response flow:

  Userspace                           Kernel
     │                                  │
     │──── NLM_F_REQUEST ──────────────►│  netlink_sendmsg()
     │     nlmsg_type = RTM_GETLINK     │  → rtnetlink dispatch
     │     nlmsg_flags = NLM_F_DUMP     │
     │                                  │
     │◄─── NLMSG_DONE / multipart ─────│  netlink_dump()
     │     nlmsg_flags = NLM_F_MULTI    │  → netlink_unicast()
     │     ...                          │
     │◄─── NLMSG_DONE ─────────────────│
     │                                  │

  Multipart dump (NLM_F_DUMP):
  ┌──────────────────────────────────────┐
  │ msg1: nlmsg_flags |= NLM_F_MULTI    │
  │ msg2: nlmsg_flags |= NLM_F_MULTI    │
  │ ...                                  │
  │ msgN: nlmsg_type = NLMSG_DONE       │
  └──────────────────────────────────────┘
```

---

## Generic Netlink (genetlink)

```
  ┌──────────────────────────────────────────────────────────────┐
  │ Generic Netlink extends AF_NETLINK with dynamic families.    │
  │ Instead of allocating a new NETLINK_* protocol number,       │
  │ subsystems register a genl_family on NETLINK_GENERIC (16).   │
  └──────────────────────────────────────────────────────────────┘

  Family Registration:

    struct genl_family my_family = {
        .name    = "MY_SUBSYSTEM",
        .version = 1,
        .maxattr = MY_ATTR_MAX,
        .ops     = my_ops,            // array of genl_ops
        .n_ops   = ARRAY_SIZE(my_ops),
    };
    genl_register_family(&my_family);  → assigns .id dynamically

  Message dispatch:

  Userspace                                    Kernel
     │                                           │
     │─ resolve family id ──────────────────────►│ genl_ctrl
     │  (CTRL_CMD_GETFAMILY, name="MY_SUBSYSTEM")│ → returns id=N
     │◄─ family_id = N ─────────────────────────│
     │                                           │
     │─ send request ──────────────────────────►│
     │  nlmsg_type = N (family id)              │ genl_rcv()
     │  genlmsghdr.cmd = MY_CMD_FOO             │  → genl_rcv_msg()
     │  + nlattr payload                        │    → my_ops[].doit()
     │                                           │
     │◄─ response / multipart ──────────────────│
     │                                           │

  struct genlmsghdr (4 bytes, after nlmsghdr):
  ┌──────────┬──────────┬──────────┐
  │  cmd     │ version  │ reserved │
  │ (8 bits) │ (8 bits) │ (16 bits)│
  └──────────┴──────────┴──────────┘
```

---

## Key Data Structures

| Structure | File | Purpose |
|-----------|------|---------|
| `struct nlmsghdr` | `include/uapi/linux/netlink.h` | 16-byte message header (len, type, flags, seq, pid) |
| `struct nlattr` | `include/uapi/linux/netlink.h` | TLV attribute header (nla_len, nla_type) |
| `struct netlink_sock` | `net/netlink/af_netlink.h` | Per-socket state: portid, groups, dump callback |
| `struct netlink_table` | `net/netlink/af_netlink.c` | Per-protocol table of netlink sockets |
| `struct netlink_callback` | `include/linux/netlink.h` | State for NLM_F_DUMP iteration |
| `struct genl_family` | `include/net/genetlink.h` | Generic netlink family: name, id, ops, policies |
| `struct genl_ops` | `include/net/genetlink.h` | Per-command handler: cmd, doit(), dumpit(), policy |
| `struct genlmsghdr` | `include/uapi/linux/genetlink.h` | Generic netlink msg header (cmd, version) |
| `struct netlink_ext_ack` | `include/linux/netlink.h` | Extended error reporting (bad attr, message) |

---

## Key Functions

### Socket Layer (`net/netlink/af_netlink.c`)

| Function | Description |
|----------|-------------|
| `netlink_create()` | Create a new `PF_NETLINK` socket (`.create` in `netlink_family_ops`) |
| `netlink_bind()` | Bind socket to portid and multicast groups |
| `netlink_sendmsg()` | Send message from userspace → kernel dispatch |
| `netlink_recvmsg()` | Deliver message from kernel → userspace |
| `netlink_unicast()` | Send skb to a single destination portid |
| `netlink_broadcast()` | Send skb to all sockets in a multicast group |
| `netlink_dump()` | Iterate dump callback for NLM_F_DUMP requests |
| `netlink_dump_start()` | Initialize dump state and begin iteration |

### Message Construction (`include/net/netlink.h`)

| Function | Description |
|----------|-------------|
| `nlmsg_new()` | Allocate a new skb for a netlink message |
| `nlmsg_put()` | Add nlmsghdr to skb |
| `nlmsg_end()` | Finalize message in skb |
| `nla_put()` | Append a TLV attribute to message |
| `nla_put_u32()` | Append a u32 attribute |
| `nla_put_string()` | Append a string attribute |
| `nla_nest_start()` | Begin a nested attribute |
| `nla_nest_end()` | Close a nested attribute |
| `nla_parse()` | Parse attributes from message into array |

### Generic Netlink (`net/netlink/genetlink.c`)

| Function | Description |
|----------|-------------|
| `genl_register_family()` | Register a new genl family (assigns dynamic id) |
| `genl_unregister_family()` | Remove a genl family |
| `genl_rcv_msg()` | Dispatch incoming genl message to family ops |
| `genlmsg_new()` | Allocate skb for genl reply |
| `genlmsg_put()` | Add nlmsghdr + genlmsghdr to skb |
| `genlmsg_reply()` | Send genl reply to requesting socket |
| `genlmsg_multicast()` | Broadcast genl message to multicast group |

---

## Practical Analogy

> **Netlink is like a postal system inside the kernel.**
>
> Each **protocol** (NETLINK_ROUTE, NETLINK_GENERIC, …) is a separate post
> office.  Userspace processes open a **mailbox** (`socket()` + `bind()`) with
> a unique **port ID**.  Messages are **letters** with a standard envelope
> (`nlmsghdr`) containing the destination, a sequence number, and flags.
> Inside the envelope is the **payload** — structured data with nested
> **labeled fields** (`nlattr` TLV attributes).
>
> **Unicast** is registered mail to one recipient.  **Broadcast** is a
> newsletter sent to every subscriber in a **multicast group**.  A **dump**
> request is like asking "send me everything" — the kernel replies with
> **multiple letters** (`NLM_F_MULTI`) followed by a final **"end of list"**
> marker (`NLMSG_DONE`).
>
> **Generic Netlink** is the post office that lets new departments register
> dynamically — instead of building a whole new post office for each kernel
> subsystem, they just open a window at the generic counter and get assigned
> a number.

---

## Configuration

- `CONFIG_NET` — basic networking (always enabled)
- `CONFIG_NETLINK_DIAG` — netlink socket diagnostics (`ss -f netlink`)
- No separate config for base netlink — it is built into the network stack.
- Generic netlink is enabled via `CONFIG_NET` automatically.

## procfs / sysfs

- `/proc/net/netlink` — lists all open netlink sockets (protocol, portid, groups)

## Userspace Tools

- **iproute2** (`ip`, `ss`, `bridge`, `tc`) — primary NETLINK_ROUTE consumer
- **libnl** / **libmnl** — C libraries for building netlink applications
- **pyroute2** — Python netlink library
- **genl-ctrl-list** (from iproute2) — list registered genetlink families
