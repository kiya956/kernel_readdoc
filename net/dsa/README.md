# DSA (Distributed Switch Architecture) Subsystem

## Overview

The **Distributed Switch Architecture (DSA)** is a Linux kernel framework for
managing embedded Ethernet switch chips commonly found in routers, access points,
and industrial gateways. DSA creates a **per-port `net_device`** for each switch
port, enabling standard Linux networking tools (`ip`, `bridge`, `tc`) to manage
switch ports as if they were individual NICs.

Key design principles:

- **Tag protocols** multiplex/demultiplex traffic for individual switch ports
  over a single CPU-facing Ethernet link (the *DSA master*).
- Each user-facing switch port appears as a standalone `net_device` (a *DSA slave*).
- Switch-specific hardware offloading is exposed through `struct dsa_switch_ops`.
- Multiple switches can be interconnected in a tree via *DSA links*, forming a
  `struct dsa_switch_tree`.

---

## Architecture Diagram

```
                        ┌──────────────────────────────────┐
                        │           User Space             │
                        │  (ip, bridge, tc, ethtool, …)    │
                        └────────┬───────┬───────┬─────────┘
                                 │       │       │
                          lan0 (swp0) lan1 (swp1) lan2 (swp2)
                          [DSA slave net_devices — one per user port]
                                 │       │       │
                        ┌────────┴───────┴───────┴─────────┐
                        │        DSA Tag Protocol           │
                        │  (prepend / append tag to frames) │
                        │  e.g. DSA, EDSA, brcm, ocelot,   │
                        │       mtk, sja1105, …             │
                        └──────────────┬───────────────────┘
                                       │  tagged frames
                        ┌──────────────┴───────────────────┐
                        │        DSA Master (eth0)          │
                        │   (real NIC facing the CPU port)  │
                        └──────────────┬───────────────────┘
                                       │  RGMII / SGMII / …
                        ┌──────────────┴───────────────────┐
                        │       Ethernet Switch Chip        │
                        │  (e.g. Marvell 88E6xxx, MT7530,  │
                        │   BCM gillnet, Microchip KSZ,    │
                        │   NXP SJA1105, Vitesse/Ocelot)   │
                        ├────────┬───────┬───────┬─────────┤
                        │ Port 0 │Port 1 │Port 2 │ Port N  │
                        └────────┴───────┴───────┴─────────┘
                            │       │       │       │
                         external user-facing Ethernet ports
```

### Packet Flow — Ingress (switch → CPU)

1. Frame arrives on a physical switch port.
2. Switch chip prepends/appends a **tag** identifying the source port.
3. Tagged frame is sent to the **CPU port** over the switch-to-CPU link.
4. The DSA master NIC driver delivers the `sk_buff` to the network stack.
5. `dsa_switch_rcv()` is called — it parses the tag, strips it, and
   delivers the frame through the correct DSA slave `net_device`.

### Packet Flow — Egress (CPU → switch)

1. Userspace or the stack transmits on a DSA slave `net_device`.
2. `dsa_slave_xmit()` inserts the appropriate tag indicating the
   destination port.
3. The tagged frame is passed to the DSA master for transmission.
4. The switch chip reads the tag and forwards the frame to the
   designated port.

---

## Key Kernel Structures

### `struct dsa_switch`

Represents a single switch chip instance.

| Field               | Description                                      |
|---------------------|--------------------------------------------------|
| `ops`               | Pointer to `struct dsa_switch_ops` callbacks      |
| `dev`               | Associated `struct device` (parent bus device)    |
| `dst`               | Back-pointer to the `dsa_switch_tree`             |
| `num_ports`         | Number of ports on this switch                    |
| `ports[]`           | Array of `struct dsa_port`                        |
| `ageing_time`       | FDB ageing time in seconds                        |

### `struct dsa_port`

Represents a single port within a switch.

| Field               | Description                                      |
|---------------------|--------------------------------------------------|
| `type`              | `DSA_PORT_TYPE_CPU`, `_DSA`, or `_USER`           |
| `slave`             | Pointer to the slave `net_device` (user ports)    |
| `master`            | Pointer to the DSA master `net_device` (CPU port) |
| `dp_flags`          | Per-port feature flags                            |
| `index`             | Port number on the switch                         |
| `bridge`            | Bridge the port is a member of (if any)           |

### `struct dsa_switch_ops`

The hardware abstraction layer. Switch drivers implement these callbacks:

| Callback                  | Purpose                                          |
|---------------------------|--------------------------------------------------|
| `setup` / `teardown`     | One-time switch init / cleanup                    |
| `port_enable` / `port_disable` | Enable or disable a port                    |
| `port_stp_state_set`     | Set STP state for a port                          |
| `port_bridge_join/leave`  | Add/remove port from a bridge                    |
| `port_fdb_add/del/dump`  | Manage the forwarding database                    |
| `port_mdb_add/del`       | Manage multicast database entries                 |
| `port_vlan_add/del`      | VLAN configuration                                |
| `phylink_get_caps`       | Report PHY link capabilities                      |
| `get_ethtool_stats`      | Provide per-port ethtool statistics               |
| `get_tag_protocol`       | Return the tag protocol to use for the CPU port   |

### `struct dsa_switch_tree`

Represents the entire topology of interconnected switches.

| Field               | Description                                      |
|---------------------|--------------------------------------------------|
| `switches`          | List of `dsa_switch` instances in the tree        |
| `ports`             | All ports across the tree                         |
| `tag_ops`           | Active tag protocol operations                    |
| `setup`             | Whether the tree has been fully set up             |

---

## Key Functions

| Function                        | File               | Purpose                                  |
|---------------------------------|---------------------|------------------------------------------|
| `dsa_switch_rcv()`             | `net/dsa/tag.c`     | Ingress: demux tagged frames to slaves   |
| `dsa_slave_xmit()`            | `net/dsa/slave.c`   | Egress: tag and forward to DSA master    |
| `dsa_tag_driver_register()`   | `net/dsa/tag.c`     | Register a tag protocol driver           |
| `dsa_register_switch()`       | `net/dsa/dsa.c`     | Register a switch with the DSA framework |
| `dsa_unregister_switch()`     | `net/dsa/dsa.c`     | Remove a switch from the DSA framework   |
| `dsa_port_enable()`           | `net/dsa/port.c`    | Enable a DSA port                        |
| `dsa_port_setup()`            | `net/dsa/port.c`    | Set up a DSA port (type-specific init)   |
| `dsa_slave_create()`          | `net/dsa/slave.c`   | Create a slave net_device for a port     |
| `dsa_tree_setup()`            | `net/dsa/dsa.c`     | Bring up an entire DSA switch tree       |
| `dsa_master_setup()`          | `net/dsa/master.c`  | Configure the DSA master net_device      |

---

## Tag Protocols

DSA uses **tag protocols** to identify which port a frame belongs to.
Each tag protocol is implemented as a `struct dsa_device_ops` and
registered via `dsa_tag_driver_register()` / `DSA_TAG_DRIVER()`.

| Tag Protocol | Kconfig Symbol              | Vendors / Chips                    |
|-------------|-----------------------------|------------------------------------|
| `DSA`       | `CONFIG_NET_DSA_TAG_DSA`    | Marvell (legacy)                   |
| `EDSA`      | `CONFIG_NET_DSA_TAG_EDSA`   | Marvell EtherType DSA              |
| `brcm`      | `CONFIG_NET_DSA_TAG_BRCM`   | Broadcom gillnet switches          |
| `brcm_prepend` | `CONFIG_NET_DSA_TAG_BRCM_PREPEND` | Broadcom (prepended tag)  |
| `ocelot`    | `CONFIG_NET_DSA_TAG_OCELOT` | Vitesse / Microsemi Ocelot         |
| `ocelot_8021q` | `CONFIG_NET_DSA_TAG_OCELOT_8021Q` | Ocelot using 802.1Q tags |
| `mtk`       | `CONFIG_NET_DSA_TAG_MTK`    | MediaTek MT7530 family             |
| `sja1105`   | `CONFIG_NET_DSA_TAG_SJA1105`| NXP SJA1105 automotive switch      |
| `ksz`       | `CONFIG_NET_DSA_TAG_KSZ`    | Microchip KSZ series               |
| `realtek`   | `CONFIG_NET_DSA_TAG_RTL4_A` / `RTL8_4` | Realtek RTL83xx/RTL93xx |
| `qca`       | `CONFIG_NET_DSA_TAG_QCA`    | Qualcomm Atheros QCA8K             |
| `trailer`   | `CONFIG_NET_DSA_TAG_TRAILER`| Marvell trailer mode               |
| `8021q`     | `CONFIG_NET_DSA_TAG_8021Q`  | Generic VLAN-based tagging         |

---

## Source Layout

```
net/dsa/
├── dsa.c             # Core: switch/tree registration, setup/teardown
├── port.c            # Per-port setup, STP, bridging, VLAN helpers
├── slave.c           # Slave net_device creation, xmit, ethtool ops
├── master.c          # DSA master device configuration
├── tag.c             # Tag protocol dispatch (dsa_switch_rcv)
├── tag_dsa.c         # Marvell DSA tag driver
├── tag_edsa.c        # Marvell EDSA tag driver
├── tag_brcm.c        # Broadcom tag drivers
├── tag_ocelot.c      # Ocelot tag driver
├── tag_mtk.c         # MediaTek tag driver
├── tag_sja1105.c     # NXP SJA1105 tag driver
├── tag_ksz.c         # Microchip KSZ tag driver
├── tag_qca.c         # Qualcomm QCA tag driver
├── tag_8021q.c       # Generic VLAN-based tagging
├── switch.c          # dsa_switch_ops dispatch and helpers
├── Makefile
└── Kconfig
```

---

## Tracing & Debug

### Key kprobes for bpftrace

```bash
# Trace every DSA frame received on the CPU port
sudo bpftrace -e 'kprobe:dsa_switch_rcv { printf("dsa_switch_rcv skb=%p\n", arg0); }'

# Trace every frame transmitted by a DSA slave
sudo bpftrace -e 'kprobe:dsa_slave_xmit { printf("dsa_slave_xmit skb=%p dev=%p\n", arg0, arg1); }'

# Trace switch registration
sudo bpftrace -e 'kprobe:dsa_register_switch { printf("switch registered ds=%p\n", arg0); }'

# Trace tag driver registration
sudo bpftrace -e 'kprobe:dsa_tag_driver_register { printf("tag driver registered\n"); }'

# Trace port enable/setup
sudo bpftrace -e 'kprobe:dsa_port_enable { printf("port enable dp=%p\n", arg0); }'
```

### Sysfs inspection

```bash
# List DSA slave interfaces
ls /sys/class/net/*/dsa/ 2>/dev/null

# Check DSA master relationship
cat /sys/class/net/<slave>/dsa/tagging

# List all DSA-related kernel symbols
grep -i dsa /proc/kallsyms | head -30
```

### Kernel config checks

```bash
# Verify DSA is enabled
grep CONFIG_NET_DSA /boot/config-$(uname -r)
```

### ftrace / tracepoints

```bash
# List DSA-related tracepoints
grep -i dsa /sys/kernel/debug/tracing/available_events 2>/dev/null

# List DSA-related tracing functions
grep -i dsa /sys/kernel/debug/tracing/available_filter_functions 2>/dev/null | head -20
```

---

## References

- `Documentation/networking/dsa/dsa.rst` — upstream kernel documentation
- `Documentation/networking/dsa/configuration.rst` — switch port config guide
- `include/net/dsa.h` — primary DSA header with all structures
- `net/dsa/` — implementation source
- `drivers/net/dsa/` — individual switch chip drivers
