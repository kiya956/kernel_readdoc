# Linux Kernel MPLS — MultiProtocol Label Switching

## Overview

**MPLS** (MultiProtocol Label Switching) is a high-performance packet
forwarding mechanism that uses **short, fixed-length labels** instead of
long network addresses to make forwarding decisions.  Rather than
performing a longest-prefix-match IP lookup at every hop, MPLS routers
(Label Switch Routers — LSRs) simply look up a local label, apply the
associated operation (**push**, **swap**, or **pop**), and forward the
packet to the next hop.  This makes forwarding faster and enables
traffic-engineering features such as explicit paths, VPNs, and fast
reroute.

The Linux kernel implements MPLS in `net/mpls/` with support for:

- **Label-based forwarding** via a per-platform label table
- **Label stack** operations: push (encapsulate), swap (relay), pop (decapsulate)
- **Multipath / ECMP** nexthops for load balancing
- **Netlink** configuration (compatible with `ip -f mpls route …`)
- **Sysctl** knobs under `/proc/sys/net/mpls/`

Source: `net/mpls/`, `include/net/mpls.h`, `include/uapi/linux/mpls.h`.

---

## Subsystem Stack — MPLS Forwarding Path

```
┌─────────────────────────────────────────────────────────────────────────┐
│                            USERSPACE                                    │
│                                                                         │
│   ip -f mpls route add 100 via inet 10.0.0.2 dev eth1                 │
│   ip -f mpls route show                                                │
│   sysctl net.mpls.platform_labels=1048575                              │
│   sysctl net.mpls.conf.<dev>.input=1   ← enable MPLS input on iface   │
│                                                                         │
└──────────────────────────────┬──────────────────────────────────────────┘
                               │ Netlink / sysctl
┌──────────────────────────────▼──────────────────────────────────────────┐
│                     MPLS ROUTE TABLE (af_mpls.c)                        │
│                                                                         │
│   platform_label[]  ← array indexed by label value                     │
│   Each slot → struct mpls_route → one or more struct mpls_nh            │
│                                                                         │
│   Netlink handlers: mpls_rtm_newroute / mpls_rtm_delroute              │
│   Sysctl: mpls_platform_labels, mpls_conf (per-device input enable)    │
└──────────────────────────────┬──────────────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────────────┐
│                     MPLS FORWARDING ENGINE                              │
│                                                                         │
│  Ingress (IP → MPLS)         LSR (MPLS → MPLS)       Egress (MPLS → IP)│
│  ┌─────────────────┐   ┌──────────────────────┐   ┌──────────────────┐ │
│  │  IP packet in   │   │  MPLS packet in      │   │  MPLS packet in  │ │
│  │       │         │   │       │               │   │       │          │ │
│  │  label PUSH     │   │  label table lookup  │   │  label POP       │ │
│  │  ┌──────────┐   │   │  label SWAP          │   │  ┌────────────┐  │ │
│  │  │ L │ IP   │   │   │  ┌──────────┐        │   │  │ IP payload │  │ │
│  │  └──────────┘   │   │  │ L'│ IP   │        │   │  └────────────┘  │ │
│  │  → nexthop      │   │  └──────────┘        │   │  → IP forwarding │ │
│  └─────────────────┘   │  → nexthop           │   └──────────────────┘ │
│                         └──────────────────────┘                        │
│                                                                         │
│  mpls_forward()  ← main receive path (registered as packet handler)    │
│  mpls_output()   ← transmit with label encapsulation                   │
│  nla_put_labels()← encode label stack into netlink attributes          │
└─────────────────────────────────────────────────────────────────────────┘
```

### Label Stack Operations

```
PUSH (ingress LER):
  [IP hdr | payload]  →  [MPLS label | IP hdr | payload]

SWAP (transit LSR):
  [MPLS label_A | IP hdr | payload]  →  [MPLS label_B | IP hdr | payload]

POP (egress LER):
  [MPLS label | IP hdr | payload]  →  [IP hdr | payload]  → IP forwarding
```

### MPLS Label Format (4 bytes)

```
 0                   1                   2                   3
 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
├─┼─┼─┼─┼─┼─┼─┼─┼─┼─┼─┼─┼─┼─┼─┼─┼─┼─┼─┼─┼─┼─┼─┼─┼─┼─┼─┼─┼─┼─┼─┼─┤
│              Label (20 bits)            │TC │S│     TTL (8 bits)  │
└─────────────────────────────────────────┴───┴─┴───────────────────┘
  Label : forwarding label (0–15 reserved, 16+ user-defined)
  TC    : Traffic Class (3 bits, for QoS / ECN)
  S     : Bottom-of-Stack (1 = last label in the stack)
  TTL   : Time-to-Live (decremented at each LSR)
```

---

## Key Data Structures

| Structure | File | Purpose |
|---|---|---|
| `struct mpls_route` | `internal.h` | One MPLS route entry: label → nexthop(s) |
| `struct mpls_nh` | `internal.h` | A single nexthop: output device, label stack, gateway |
| `struct mpls_entry_decoded` | `internal.h` | Decoded MPLS shim header (label, TC, S, TTL) |
| `struct mpls_dev` | `internal.h` | Per-netdevice MPLS state (input enable, stats) |
| `struct mpls_shim_hdr` | `mpls.h` | Raw 4-byte MPLS label entry on the wire |

### struct mpls_route

```c
struct mpls_route {
    struct rcu_head         rt_rcu;
    u8                      rt_protocol;
    u8                      rt_payload_type;   /* MPT_IPV4, MPT_IPV6, MPT_UNSPEC */
    u8                      rt_max_alen;
    u8                      rt_ttl_propagate;
    u8                      rt_nhn;            /* number of nexthops */
    u8                      rt_nhn_alive;
    u8                      rt_nh_size;
    struct mpls_nh          rt_nh[];           /* flexible array of nexthops */
};
```

### struct mpls_nh

```c
struct mpls_nh {  /* one nexthop for a label route */
    struct net_device __rcu *nh_dev;
    unsigned int            nh_flags;
    u8                      nh_labels;    /* number of labels to push */
    u8                      nh_via_alen;
    u8                      nh_via_table;
    u8                      nh_reserved1;
    u32                     nh_label[];   /* outgoing label stack */
    /* followed by: nh_via (gateway address) */
};
```

### struct mpls_entry_decoded

```c
struct mpls_entry_decoded {
    u32     label;
    u8      ttl;
    u8      tc;
    u8      bos;  /* bottom-of-stack bit */
};
```

---

## Key Functions

| Function | File | Purpose |
|---|---|---|
| `mpls_forward()` | `af_mpls.c` | Main MPLS receive handler — lookup label, swap/pop, forward |
| `mpls_rt_alloc()` | `af_mpls.c` | Allocate a new `mpls_route` with N nexthops |
| `mpls_output()` | `af_mpls.c` | Build MPLS header and transmit (used by LWTUNNEL) |
| `mpls_entry_decode()` | `internal.h` | Decode raw 4-byte shim header into `mpls_entry_decoded` |
| `mpls_entry_encode()` | `internal.h` | Encode label/tc/s/ttl back into wire format |
| `nla_put_labels()` | `af_mpls.c` | Encode label stack into netlink attribute |
| `nla_get_labels()` | `af_mpls.c` | Decode label stack from netlink attribute |
| `mpls_netconf_dump_devconf()` | `af_mpls.c` | Dump per-device MPLS netconf settings via netlink |
| `mpls_route_input()` | `af_mpls.c` | Validate inbound MPLS and invoke `mpls_forward()` |

### mpls_forward() — Core Forwarding Path

```
mpls_forward(skb):
  1. Decode top-of-stack label  →  mpls_entry_decoded
  2. Lookup label in platform_label[] table
  3. If no route → drop
  4. Select nexthop (ECMP hash if multipath)
  5. Decrement TTL → if 0, send ICMP TTL exceeded
  6. If nh_labels == 0  → POP (strip MPLS, deliver to IP)
     If bos             → SWAP top label
     Else               → SWAP / PUSH labels from nh_label[]
  7. Set output device from nexthop
  8. Transmit via dev_queue_xmit()
```

---

## Sysctl Knobs

| Path | Purpose |
|---|---|
| `/proc/sys/net/mpls/platform_labels` | Max label value (0 = MPLS disabled) |
| `/proc/sys/net/mpls/ip_ttl_propagate` | Copy IP TTL into MPLS TTL on push (1=yes) |
| `/proc/sys/net/mpls/default_ttl` | Default MPLS TTL when not propagating |
| `/proc/sys/net/mpls/conf/<dev>/input` | Enable MPLS input processing on a device |

---

## Key Source Files

| File | Purpose |
|---|---|
| `net/mpls/af_mpls.c` | Core: forwarding, route table, netlink, sysctl |
| `net/mpls/internal.h` | Internal data structures and helpers |
| `net/mpls/mpls_gso.c` | GSO (segmentation offload) for MPLS |
| `net/mpls/mpls_iptunnel.c` | MPLS-in-IP lightweight tunnel (LWTUNNEL) |
| `include/net/mpls.h` | Public kernel API |
| `include/uapi/linux/mpls.h` | UAPI: label constants, netlink attributes |

---

## Tracing & Debugging

### bpftrace — Trace MPLS Forwarding

```bash
# Trace every MPLS packet forwarded
sudo bpftrace -e '
kprobe:mpls_forward {
    printf("mpls_forward dev=%s skb=%p\n",
           str(((struct net_device *)arg0)->name), arg1);
}'

# Trace route allocation
sudo bpftrace -e '
kretprobe:mpls_rt_alloc {
    printf("mpls_rt_alloc returned %p\n", retval);
}'

# Trace MPLS output (label push via LWTUNNEL)
sudo bpftrace -e '
kprobe:mpls_output {
    printf("mpls_output skb=%p\n", arg1);
}'
```

### Useful Commands

```bash
# Show MPLS routing table
ip -f mpls route show

# Add a label swap route (label 100 → swap to 200, forward via eth1)
ip -f mpls route add 100 as 200 via inet 10.0.0.2 dev eth1

# Add a label pop route (label 300 → pop, deliver to IP)
ip -f mpls route add 300 via inet 10.0.0.1 dev eth0

# Enable MPLS on an interface
sysctl -w net.mpls.conf.eth0.input=1

# Set platform label range
sysctl -w net.mpls.platform_labels=1048575

# Check if mpls_router module is loaded
lsmod | grep mpls

# Check MPLS symbols
grep mpls_ /proc/kallsyms | head -20
```

---

## Analogy

MPLS is like an **airport baggage handling system**:

- Each bag (packet) gets a **tag** (label) at check-in (ingress LER).
- Conveyor belt junctions (LSRs) read only the tag to decide which belt
  to route the bag onto — they **swap** the tag for the next segment.
- At the destination carousel (egress LER), the tag is **removed** (popped)
  and the bag is delivered by its actual address (IP forwarding).
- This is much faster than opening each bag to read the destination address
  (longest-prefix-match IP lookup) at every junction.

---

## References

- `net/mpls/af_mpls.c` — Full forwarding and route management implementation
- `Documentation/networking/mpls-sysctl.rst` — Sysctl documentation
- `include/uapi/linux/mpls.h` — Label constants and netlink API
- RFC 3031 — MPLS Architecture
- RFC 3032 — MPLS Label Stack Encoding
