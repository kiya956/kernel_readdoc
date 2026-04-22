# L3 Master Device (VRF) — Layer 3 Isolation API

## Overview

**L3 Master Device (l3mdev)** is a Linux kernel API that allows **Virtual
Routing and Forwarding (VRF)** devices to intercept and redirect Layer 3
(IP) traffic into isolated routing table domains.

The primary (and so far only) l3mdev type is **VRF** (`ip link add vrf0 type
vrf table 100`).  By enslaving network interfaces to a VRF device:

- All traffic arriving on enslaved interfaces is looked up in the VRF's
  private FIB table rather than the global one.
- Sockets bound to a VRF device are naturally scoped to that VRF's routes.
- Multiple tenants / management networks can coexist on the same host without
  route leakage.

Source: `net/l3mdev/l3mdev.c`, `drivers/net/vrf.c`, `include/net/l3mdev.h`.

---

## Subsystem Stack

```
┌────────────────────────────────────────────────────────────────┐
│                        USERSPACE                               │
│  iproute2:  ip link add vrf0 type vrf table 100               │
│             ip link set eth0 master vrf0                       │
│             ip route add 10.0.0.0/8 via 192.168.1.1 vrf vrf0  │
│  ss / ip socket:  bind socket to vrf0 via SO_BINDTODEVICE     │
└──────────────────────────────┬─────────────────────────────────┘
                               │
┌──────────────────────────────▼─────────────────────────────────┐
│              VRF NETDEV DRIVER  (drivers/net/vrf.c)            │
│                                                                 │
│  vrf_dev_xmit() — packets from enslaved dev enter VRF lookup  │
│  Implements l3mdev_ops:                                        │
│   .l3mdev_fib_table()  → returns VRF's table ID               │
│   .l3mdev_l3_rcv()     → Rx hook: re-route in VRF context     │
│   .l3mdev_l3_out()     → Tx hook: re-route in VRF context     │
│  Creates per-VRF FIB table (ip_route_output_flow VRF table)   │
└──────────────────────────────┬─────────────────────────────────┘
                               │ l3mdev_ops callbacks
┌──────────────────────────────▼─────────────────────────────────┐
│           L3MDEV CORE API  (net/l3mdev/l3mdev.c)               │
│                                                                 │
│  l3mdev_master_ifindex_rcu(dev)                               │
│    → returns master (VRF) ifindex for a enslaved device        │
│                                                                 │
│  l3mdev_fib_table(dev)                                        │
│    → returns FIB table ID for the master device                │
│                                                                 │
│  l3mdev_l3_rcv(dev, skb, proto)                               │
│    → calls VRF .l3mdev_l3_rcv hook on RX                      │
│                                                                 │
│  l3mdev_l3_out(dev, sk, skb, proto)                           │
│    → calls VRF .l3mdev_l3_out hook on TX                      │
│                                                                 │
│  l3mdev_update_flow(net, fl)                                  │
│    → updates flowi with VRF table/oif for routing lookups      │
│                                                                 │
│  l3mdev_fib_rule_match()                                      │
│    → FIB rule callback so policy routing respects VRF          │
└──────────────────────────────┬─────────────────────────────────┘
                               │ called from IPv4 / IPv6 routing
┌──────────────────────────────▼─────────────────────────────────┐
│             IP ROUTING  (net/ipv4/fib_*, net/ipv6/ip6_fib*)    │
│                                                                 │
│  ip_route_input_slow():  checks l3mdev_update_flow()          │
│  ip_route_output_flow(): checks l3mdev_fib_table()            │
│  IPv6 addrconf / route:  respects l3mdev master               │
└──────────────────────────────┬─────────────────────────────────┘
                               │
┌──────────────────────────────▼─────────────────────────────────┐
│          ENSLAVED INTERFACES  (eth0, eth1, …)                  │
│  Traffic arrives/leaves on physical interfaces                 │
│  netdev->master = vrf0  →  l3mdev APIs kick in for routing    │
└────────────────────────────────────────────────────────────────┘
```

---

## VRF Packet Walk-through (RX)

```
Packet arrives on eth0 (enslaved to vrf0, table 100)
         │
         ▼
ip_rcv() → l3mdev_l3_rcv(eth0, skb, AF_INET)
         │
         ▼  (VRF driver hook)
vrf_l3_rcv() → re-injects skb with vrf0 as input device
         │
         ▼
ip_route_input_slow() → l3mdev_update_flow() → table_id = 100
         │
         ▼
FIB lookup in table 100 → local delivery or forward within VRF
```

---

## Key Data Structures

| Structure | Purpose |
|---|---|
| `l3mdev_ops` | VRF driver vtable (fib_table, l3_rcv, l3_out, link_scope_lookup) |
| `flowi` | Flow info passed to FIB; l3mdev sets `flowi_l3mdev` field |
| VRF private data | In `drivers/net/vrf.c`: table ID, route caches, stats |

---

## Key Source Files

| File | Purpose |
|---|---|
| `net/l3mdev/l3mdev.c` | l3mdev API: master ifindex, FIB table, flow hooks |
| `include/net/l3mdev.h` | l3mdev_ops vtable, API prototypes |
| `drivers/net/vrf.c` | VRF net_device driver (the only l3mdev type so far) |
| `net/ipv4/fib_rules.c` | FIB rule integration with l3mdev |
| `net/ipv6/ip6_fib.c` | IPv6 FIB + l3mdev support |

---

## Analogy

l3mdev / VRF is like **separate post offices in the same building**:

- Each VRF is a **private post office** with its own address book (routing table).
- Letters arriving at a counter (enslaved interface) are automatically
  handed to the right post office (VRF) rather than the general desk.
- The **l3mdev API** is the building's intercom: when the IP stack needs to
  know "which routing table should I use for this interface?", it rings the
  l3mdev API and gets the answer.
- Without VRF, all letters share one address book — inter-tenant route leakage.

---

## References

- `include/net/l3mdev.h` — API
- `net/l3mdev/l3mdev.c` — core
- `drivers/net/vrf.c` — VRF driver
- `Documentation/networking/vrf.rst`
