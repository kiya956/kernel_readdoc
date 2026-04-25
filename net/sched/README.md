# Linux Kernel Traffic Control (TC) — Queueing Disciplines & Packet Scheduling

## Overview

**Traffic Control (TC)** is the kernel subsystem for packet scheduling,
shaping, policing, and classification on network interfaces. It implements
**queueing disciplines (qdiscs)** that control how packets are enqueued,
classified, and dequeued for transmission.

Key concepts:
- **Qdiscs** — scheduling algorithms attached to interfaces (root, ingress, child)
- **Classes** — hierarchical subdivisions within classful qdiscs (HTB, HFSC, CBQ)
- **Filters** — classify packets into classes (u32, flower, bpf, cgroup)
- **Actions** — modify or redirect packets (mirred, gact, pedit, nat)

Source: `net/sched/`, `include/net/sch_generic.h`, `include/net/pkt_cls.h`.

Common qdiscs: `pfifo_fast` (default), `fq_codel`, `htb`, `tbf`, `netem`,
`ingress`, `clsact`, `fq`, `cake`, `red`, `sfq`.

---

## Subsystem Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                         USERSPACE                                │
│                                                                  │
│  tc (iproute2)                                                   │
│   tc qdisc add dev eth0 root htb default 10                      │
│   tc class add dev eth0 parent 1: classid 1:1 htb rate 100mbit   │
│   tc filter add dev eth0 parent 1: protocol ip prio 1            │
│        flower dst_ip 10.0.0.0/24 action mirred egress redirect   │
│   tc qdisc add dev eth0 parent 1:1 handle 10: fq_codel           │
└──────────────────────────────┬───────────────────────────────────┘
                               │  Netlink (RTM_NEWQDISC, RTM_NEWTCLASS, ...)
┌──────────────────────────────▼───────────────────────────────────┐
│                    TC NETLINK INTERFACE                          │
│                   (net/sched/sch_api.c)                           │
│                                                                  │
│  tc_modify_qdisc()   — add/change/replace qdisc                 │
│  tc_ctl_tclass()     — add/change/delete class                   │
│  tc_new_tfilter()    — add/change filter                         │
│  tc_ctl_action()     — add/change action                         │
└──────────────────────────────┬───────────────────────────────────┘
                               │
┌──────────────────────────────▼───────────────────────────────────┐
│                     TX PACKET PATH                               │
│                                                                  │
│  dev_queue_xmit()                                                │
│    │                                                             │
│    ▼                                                             │
│  __dev_xmit_skb()                                                │
│    │                                                             │
│    ▼                                                             │
│  ┌───────────────────────────────────────────────────────────┐   │
│  │  ROOT QDISC  (e.g., HTB)                                  │   │
│  │  qdisc_enqueue()  → htb_enqueue()                         │   │
│  │                                                            │   │
│  │  ┌──────────┐  tcf_classify()  ┌──────────┐               │   │
│  │  │ FILTERS  │ ←────────────── │  skb      │               │   │
│  │  │ u32      │  classid result  │ (packet)  │               │   │
│  │  │ flower   │──────────────►  │           │               │   │
│  │  │ bpf      │                  └──────────┘               │   │
│  │  └──────────┘                                              │   │
│  │       │                                                    │   │
│  │       ▼                                                    │   │
│  │  ┌──────────┐    ┌──────────┐    ┌──────────┐             │   │
│  │  │ Class 1:1│    │ Class 1:2│    │ Class 1:3│             │   │
│  │  │ rate 50M │    │ rate 30M │    │ rate 20M │             │   │
│  │  └────┬─────┘    └────┬─────┘    └────┬─────┘             │   │
│  │       │               │               │                    │   │
│  │       ▼               ▼               ▼                    │   │
│  │  ┌──────────┐    ┌──────────┐    ┌──────────┐             │   │
│  │  │ LEAF     │    │ LEAF     │    │ LEAF     │             │   │
│  │  │ fq_codel │    │ fq_codel │    │ pfifo    │             │   │
│  │  └──────────┘    └──────────┘    └──────────┘             │   │
│  └───────────────────────────────────────────────────────────┘   │
│                                                                  │
│  __qdisc_run()                                                   │
│    │                                                             │
│    ▼                                                             │
│  qdisc_dequeue() → htb_dequeue() → child.dequeue()              │
│    │                                                             │
│    ▼                                                             │
│  sch_direct_xmit()  → dev_hard_start_xmit() → NIC driver        │
└──────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────┐
│                     RX / INGRESS PATH                            │
│                                                                  │
│  NIC driver → netif_receive_skb() → sch_handle_ingress()        │
│    │                                                             │
│    ▼                                                             │
│  ┌────────────────────────────────────────────────────────┐      │
│  │  INGRESS / CLSACT QDISC                                │      │
│  │  Filters only (no queuing) — classify, police, mirror   │      │
│  │  tcf_classify() → action chain → drop / pass / redirect │      │
│  └────────────────────────────────────────────────────────┘      │
└──────────────────────────────────────────────────────────────────┘
```

---

## Common Queueing Disciplines

| Qdisc         | Type       | File              | Description                              |
|---------------|------------|-------------------|------------------------------------------|
| `pfifo_fast`  | Classless  | sch_generic.c     | Default 3-band priority FIFO             |
| `fq_codel`    | Classless  | sch_fq_codel.c    | Fair Queuing + CoDel AQM (bufferbloat)   |
| `fq`          | Classless  | sch_fq.c          | Fair Queuing with pacing (TCP)           |
| `htb`         | Classful   | sch_htb.c         | Hierarchical Token Bucket (rate shaping) |
| `tbf`         | Classless  | sch_tbf.c         | Token Bucket Filter (simple rate limit)  |
| `netem`       | Classless  | sch_netem.c       | Network Emulator (delay, loss, jitter)   |
| `cake`        | Classless  | sch_cake.c        | Common Applications Kept Enhanced        |
| `red`         | Classless  | sch_red.c         | Random Early Detection                   |
| `sfq`         | Classless  | sch_sfq.c         | Stochastic Fairness Queuing              |
| `ingress`     | Special    | sch_ingress.c     | Ingress hook for filters (no queuing)    |
| `clsact`      | Special    | sch_ingress.c     | Ingress + egress filter-only qdisc       |

## TC Filters

| Filter    | File              | Description                                  |
|-----------|-------------------|----------------------------------------------|
| `u32`     | cls_u32.c         | Universal 32-bit key-based matching          |
| `flower`  | cls_flower.c      | Flow-based matching (5-tuple, VLAN, etc.)    |
| `bpf`     | cls_bpf.c         | eBPF program as classifier                   |
| `cgroup`  | cls_cgroup.c      | Classify by cgroup membership                |
| `route`   | cls_route.c       | Classify by routing table entry              |
| `fw`      | cls_fw.c          | Classify by fwmark (iptables MARK)           |

## TC Actions

| Action    | File               | Description                                 |
|-----------|--------------------|---------------------------------------------|
| `mirred`  | act_mirred.c       | Mirror or redirect packet to another iface  |
| `gact`    | act_gact.c         | Generic action (drop, pass, pipe, etc.)     |
| `pedit`   | act_pedit.c        | Packet header editing                       |
| `nat`     | act_nat.c          | Stateless NAT                               |
| `skbedit` | act_skbedit.c      | Edit skb metadata (mark, prio, queue)       |
| `police`  | act_police.c       | Rate policing (drop excess traffic)         |

---

## Key Data Structures

### `struct Qdisc` (`include/net/sch_generic.h`)
Core queueing discipline structure, one per TX queue.

| Field              | Description                                       |
|--------------------|---------------------------------------------------|
| `enqueue`          | Function pointer: enqueue skb                     |
| `dequeue`          | Function pointer: dequeue next skb                |
| `ops`              | `struct Qdisc_ops` — qdisc operations table       |
| `handle`           | TC handle (major:minor)                           |
| `parent`           | Parent qdisc/class handle                         |
| `dev_queue`        | Associated `netdev_queue`                         |
| `q`                | `struct qdisc_skb_head` — packet queue            |
| `qstats`           | Queue statistics (qlen, backlog, drops, overlimits)|

### `struct tcf_proto` (`include/net/pkt_cls.h`)
A filter protocol instance attached to a qdisc/class.

| Field              | Description                                       |
|--------------------|---------------------------------------------------|
| `ops`              | `struct tcf_proto_ops` — filter operations         |
| `classify`         | Classification function pointer                   |
| `protocol`         | Ethernet protocol to match (ETH_P_IP, etc.)       |
| `prio`             | Filter priority                                   |
| `chain`            | `struct tcf_chain` — chain this filter belongs to  |

### `struct tc_action` (`include/net/act_api.h`)
An action attached to a filter.

| Field              | Description                                       |
|--------------------|---------------------------------------------------|
| `ops`              | `struct tc_action_ops` — action operations         |
| `tcfa_index`       | Action index                                      |
| `tcfa_action`      | Result: TC_ACT_OK, TC_ACT_SHOT, TC_ACT_PIPE, ... |

---

## Key Functions

| Function                  | File            | Purpose                              |
|---------------------------|-----------------|--------------------------------------|
| `__qdisc_run()`           | sch_generic.c   | Main TX dequeue loop                 |
| `qdisc_enqueue_root()`    | sch_generic.c   | Enqueue skb into root qdisc         |
| `sch_direct_xmit()`       | sch_generic.c   | Direct transmit from qdisc          |
| `tcf_classify()`          | cls_api.c       | Run filter chain on skb             |
| `tc_modify_qdisc()`       | sch_api.c       | Netlink: add/modify qdisc           |
| `htb_enqueue()`           | sch_htb.c       | HTB qdisc enqueue                   |
| `fq_codel_enqueue()`      | sch_fq_codel.c  | FQ-CoDel enqueue                    |
| `pfifo_fast_enqueue()`    | sch_generic.c   | pfifo_fast enqueue                  |
| `cls_bpf_classify()`      | cls_bpf.c       | eBPF classifier                     |
| `sch_handle_ingress()`    | sch_api.c       | Ingress path filter hook            |

---

## Configuration & Observability

```
CONFIG_NET_SCHED=y          # Core TC support
CONFIG_NET_SCH_HTB=m        # HTB qdisc
CONFIG_NET_SCH_FQ_CODEL=y   # FQ-CoDel (usually default)
CONFIG_NET_SCH_NETEM=m       # Network emulator
CONFIG_NET_SCH_INGRESS=m     # Ingress/clsact qdisc
CONFIG_NET_CLS_U32=m         # u32 classifier
CONFIG_NET_CLS_FLOWER=m      # flower classifier
CONFIG_NET_CLS_BPF=m         # BPF classifier
CONFIG_NET_ACT_MIRRED=m      # mirred action
```

### procfs / sysfs

| Path                       | Description                                |
|----------------------------|--------------------------------------------|
| `/proc/net/psched`         | TC clock parameters (ticks/usec)           |
| `tc -s qdisc show`        | Per-qdisc statistics                       |
| `tc -s class show`        | Per-class statistics                       |
| `tc -s filter show`       | Per-filter hit counts                      |

---

## Typical Workflow

1. **Default state** — `pfifo_fast` or `fq_codel` root qdisc on each interface
2. **Replace root qdisc** — `tc qdisc add dev eth0 root htb`
3. **Add classes** — `tc class add ... htb rate 50mbit ceil 100mbit`
4. **Add filters** — `tc filter add ... flower dst_ip 10.0.0.0/24 classid 1:1`
5. **Add leaf qdiscs** — `tc qdisc add ... parent 1:1 fq_codel`
6. **Traffic flows** — `dev_queue_xmit()` → `htb_enqueue()` → `tcf_classify()`
   → class selected → `fq_codel_enqueue()` → `__qdisc_run()` → `htb_dequeue()`
   → `sch_direct_xmit()` → driver

---

## Tracing & Debugging

```bash
# Watch qdisc run
bpftrace -e 'kprobe:__qdisc_run { printf("qdisc_run pid=%d\n", pid); }'

# Trace enqueue
bpftrace -e 'kprobe:htb_enqueue { printf("HTB enqueue\n"); }'

# Trace classification
bpftrace -e 'kprobe:tcf_classify { printf("tc classify\n"); }'

# Show qdisc stats
tc -s qdisc show dev eth0
tc -s class show dev eth0

# Check TC clock
cat /proc/net/psched
```
