# HSR / PRP — High-availability Seamless Redundancy

## Overview

HSR (High-availability Seamless Redundancy) and PRP (Parallel Redundancy
Protocol) are defined by **IEC 62439-3** and provide **zero-failover-time
redundancy** for industrial Ethernet networks.  Unlike classical
redundancy schemes that rely on spanning-tree reconvergence, HSR/PRP
guarantees that a single link or node failure never interrupts traffic
because every frame is sent over two independent paths simultaneously.

HSR is used in safety-critical environments such as power-grid
substations (IEC 61850), factory automation, and railway signalling
where any packet loss during a failover is unacceptable.

The Linux implementation lives in **`net/hsr/`** and supports both HSR
(ring topology) and PRP (two parallel LANs) modes.

---

## How It Works

### HSR (Ring Topology)

1. A **DANH** (Doubly Attached Node implementing HSR) is connected to a
   ring through two Ethernet ports — **Port A** and **Port B**.
2. When a frame is sent, the node **duplicates** it and injects one copy
   clockwise (Port A) and one copy counter-clockwise (Port B).
3. Every node on the ring forwards frames it receives on one port out the
   other port — unless the frame originated from itself.
4. The **receiver** accepts the first copy that arrives and **discards
   the duplicate** using a per-source sequence number tracked in the
   node table.
5. If one segment of the ring fails, frames still arrive via the
   surviving path — hence **zero switchover time**.

### PRP (Parallel LANs)

1. A **DANP** (Doubly Attached Node implementing PRP) connects to two
   completely independent Ethernet LANs — **LAN A** and **LAN B**.
2. Frames are duplicated across both LANs; the receiver discards the
   duplicate using the same sequence-number mechanism.
3. Standard (singly-attached) nodes see normal Ethernet traffic on
   their LAN and remain unaware of the redundancy.

### Supervision Frames

Both HSR and PRP nodes periodically send **supervision frames**
(multicast, EtherType 0x88FB) so that every node can build and maintain
a **node table** mapping source MAC addresses to the ports through which
they are reachable.  Stale entries are aged out after a configurable
timeout (default: ~60 s per the standard).

---

## Key Kernel Structures

| Structure | Header | Purpose |
|-----------|--------|---------|
| `struct hsr_priv` | `hsr_main.h` | Per-HSR-device private data: port list, node table, sequence counter, supervision timer, protocol version |
| `struct hsr_port` | `hsr_main.h` | Represents one port (master / slave-A / slave-B); wraps the real `net_device` and links back to `hsr_priv` |
| `struct hsr_node` | `hsr_framereg.h` | Entry in the node table — stores MAC addresses (addr_A, addr_B), last sequence number, timestamps for duplicate detection and ageing |
| `struct hsr_sup_tag` | `hsr_main.h` | Parsed supervision frame tag — path, version, and sequence fields used during supervision processing |
| `struct hsr_tag` | `hsr_main.h` | The 6-byte HSR tag prepended to Ethernet frames (path, LSDU size, sequence number) |

---

## Key Functions

### Frame Forwarding

| Function | File | Role |
|----------|------|------|
| `hsr_forward_skb()` | `hsr_forward.c` | Core forwarding engine — decides whether to forward, deliver locally, or drop a frame based on port type and duplicate status |
| `hsr_handle_frame()` | `hsr_slave.c` | rx_handler registered on slave devices; entry point for every frame arriving on a slave port |

### Device Setup / Teardown

| Function | File | Role |
|----------|------|------|
| `hsr_dev_setup()` | `hsr_device.c` | net_device setup callback — configures the HSR virtual interface (flags, header ops, ethtool ops) |
| `hsr_dev_finalize()` | `hsr_device.c` | Completes device registration: attaches slave ports, starts supervision timer, adds netdev to global list |
| `hsr_dev_open()` / `hsr_dev_close()` | `hsr_device.c` | Brings slave ports up/down in sync with the HSR master device |

### Node Table / Duplicate Detection

| Function | File | Role |
|----------|------|------|
| `hsr_add_node()` | `hsr_framereg.c` | Inserts a newly seen source MAC into the node table |
| `hsr_register_frame_in()` | `hsr_framereg.c` | Records incoming sequence number; returns whether the frame is a duplicate |
| `hsr_handle_sup_frame()` | `hsr_framereg.c` | Processes received supervision frames — creates/refreshes node entries |
| `hsr_prune_nodes()` | `hsr_framereg.c` | Timer-driven garbage collector for the node table |

### Netlink Interface

| Function | File | Role |
|----------|------|------|
| `hsr_newlink()` | `hsr_netlink.c` | RTNL newlink handler — creates a new HSR/PRP device from user-space (`ip link add type hsr`) |
| `hsr_get_node_list()` | `hsr_netlink.c` | Dumps the current node table to user-space via Generic Netlink |

---

## HSR vs PRP — Key Differences

| Aspect | HSR | PRP |
|--------|-----|-----|
| Topology | Ring (daisy-chain) | Two independent parallel LANs |
| Tag location | **Prepended** to the Ethernet frame (HSR tag, 6 bytes) | **Appended** as a Redundancy Control Trailer (RCT, 6 bytes) |
| EtherType | 0x892F (HSR) | Uses original EtherType; trailer is transparent |
| Interoperability | Only HSR-capable nodes on the ring | Singly-attached nodes work on each LAN unmodified |
| Kernel config | `CONFIG_HSR` | `CONFIG_HSR` (same module handles both; mode set at link creation) |
| Link creation | `ip link add … type hsr` | `ip link add … type hsr proto 1` (proto 1 = PRP) |

---

## Source Layout — `net/hsr/`

```
net/hsr/
├── hsr_main.h          # Core data structures and constants
├── hsr_main.c          # Module init, supervision timer, global device list
├── hsr_device.c        # net_device operations (setup, open, xmit, finalize)
├── hsr_slave.c         # Slave-port rx_handler registration and frame reception
├── hsr_forward.c       # Frame forwarding and duplicate discard logic
├── hsr_framereg.c      # Node table management and duplicate detection
├── hsr_netlink.c       # RTNL and Generic Netlink interface
├── hsr_debugfs.c       # Optional debugfs node-table dump (CONFIG_HSR_DEBUG)
└── Makefile
```

---

## Tracing and Debugging

### Dynamic Debug

```bash
# Enable all pr_debug messages in the HSR subsystem
echo 'module hsr +p' > /sys/kernel/debug/dynamic_debug/control
```

### Key Tracing Points (kprobe / bpftrace)

```bash
# Trace every forwarded HSR frame
sudo bpftrace -e 'kprobe:hsr_forward_skb { printf("fwd skb=%p port=%d\n", arg0, arg1); }'

# Watch node table additions
sudo bpftrace -e 'kprobe:hsr_add_node { printf("new node on dev=%p\n", arg0); }'

# Supervision frame handling
sudo bpftrace -e 'kprobe:hsr_handle_sup_frame { printf("sup frame pid=%d\n", pid); }'
```

### debugfs (when `CONFIG_HSR_DEBUG=y`)

```bash
# Dump the node table for hsr0
cat /sys/kernel/debug/hsr/hsr0/node_table
```

### iproute2 Inspection

```bash
# List HSR/PRP interfaces
ip -d link show type hsr

# Show the node table via netlink
bridge fdb show dev hsr0        # (limited)
# Or use the hsr-specific generic-netlink tool if available
```

---

## References

- IEC 62439-3: *Industrial communication networks — High availability
  automation networks — Part 3: Parallel Redundancy Protocol (PRP) and
  High-availability Seamless Redundancy (HSR)*
- `Documentation/networking/hsr.rst` in the kernel tree
- `tools/testing/selftests/net/hsr/` — upstream self-tests
