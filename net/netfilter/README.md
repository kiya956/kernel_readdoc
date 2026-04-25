# net/netfilter — Linux Netfilter Subsystem

## Overview

`net/netfilter/` implements the **Netfilter framework**, the kernel's primary
packet filtering, connection tracking, Network Address Translation (NAT), and
packet mangling infrastructure. It provides:

- **Hook framework** (`core.c`) — five protocol-independent hook points
  (PREROUTING, INPUT, FORWARD, OUTPUT, POSTROUTING) where kernel modules can
  inspect and modify every packet traversing the stack.
- **Connection tracking** (`nf_conntrack_core.c`) — stateful packet inspection
  that associates each packet with a tracked connection, enabling stateful
  firewalling, NAT, and protocol helpers.
- **NAT engine** (`nf_nat_core.c`) — source NAT (SNAT/masquerade), destination
  NAT (DNAT/redirect), and port translation, tightly integrated with conntrack.
- **nftables** (`nf_tables_api.c`, `nft_*.c`) — the modern replacement for
  iptables, using a register-based virtual machine to evaluate rules expressed
  as chains of expressions.
- **iptables (legacy)** (`ip_tables.c`, `xt_*.c`) — the classic match/target
  table-based filtering still widely deployed.
- **Logging** (`nf_log.c`, `nf_log_syslog.c`) — kernel-level packet logging
  via syslog or NFLOG.
- **Connection tracking helpers** (`nf_conntrack_ftp.c`, `nf_conntrack_sip.c`,
  etc.) — application-layer gateways (ALGs) that parse protocols with embedded
  addresses (FTP, SIP, TFTP) to create RELATED expectations.

Source: `net/netfilter/*.c`, `include/linux/netfilter.h`,
`include/net/netfilter/nf_conntrack.h`, `include/net/netfilter/nf_tables.h`.

---

## Netfilter Hook Points in the Packet Path

Every IPv4/IPv6 packet traverses a well-defined sequence of hook points.
Registered hook functions (iptables rules, nftables chains, conntrack, NAT)
execute at each point.

```
                           INGRESS
                              │
                              ▼
                    ┌───────────────────┐
                    │   NF_INET_        │
                    │   PREROUTING      │◄── conntrack (defrag + lookup)
                    │                   │◄── DNAT / redirect
                    │                   │◄── raw table (NOTRACK)
                    └────────┬──────────┘
                             │
                     ┌───────▼───────┐
                     │  ROUTING      │
                     │  DECISION     │
                     └───┬───────┬───┘
                         │       │
              ┌──────────▼──┐  ┌─▼──────────────┐
              │ for local   │  │ for forwarding  │
              │ delivery    │  │                 │
              ▼             │  ▼                 │
    ┌──────────────────┐   │  ┌──────────────────┐
    │  NF_INET_INPUT   │   │  │  NF_INET_FORWARD │
    │                  │   │  │                  │
    │  filter/mangle   │   │  │  filter/mangle   │
    │  conntrack confirm│  │  │  security        │
    └────────┬─────────┘   │  └────────┬─────────┘
             │             │           │
             ▼             │           ▼
    ┌──────────────────┐   │  ┌───────────────────┐
    │  LOCAL PROCESS   │   │  │  NF_INET_         │
    │                  │   │  │  POSTROUTING      │
    │  Application     │   │  │                   │◄── SNAT / masquerade
    │  recv() / send() │   │  │  conntrack confirm│
    └────────┬─────────┘   │  └────────┬──────────┘
             │             │           │
             ▼             │           ▼
    ┌──────────────────┐   │       EGRESS
    │  NF_INET_OUTPUT  │   │    (forwarded pkts)
    │                  │   │
    │  filter/mangle   │   │
    │  DNAT (reroute)  │   │
    │  raw table       │   │
    └────────┬─────────┘   │
             │             │
             ▼             │
    ┌───────────────────┐  │
    │  ROUTING          │  │
    │  DECISION         │  │
    └────────┬──────────┘  │
             │             │
             ▼             │
    ┌───────────────────┐  │
    │  NF_INET_         │  │
    │  POSTROUTING      │◄─┘
    │                   │◄── SNAT / masquerade
    │  conntrack confirm│
    └────────┬──────────┘
             │
             ▼
          EGRESS
       (local pkts)
```

Hook return values control packet fate:

| Verdict         | Value | Meaning                                           |
|-----------------|-------|---------------------------------------------------|
| `NF_DROP`       | 0     | Silently discard the packet                       |
| `NF_ACCEPT`     | 1     | Continue to next hook function / accept            |
| `NF_STOLEN`     | 2     | Hook took ownership; netfilter forgets the packet  |
| `NF_QUEUE`      | 3     | Queue packet to userspace (NFQUEUE)                |
| `NF_REPEAT`     | 4     | Call this hook function again                      |
| `NF_STOP`       | 5     | Accept and skip remaining hooks at this point      |

---

## nftables Evaluation Pipeline

nftables replaces iptables with a flexible VM-based architecture. Packets are
evaluated through a hierarchy of tables, chains, rules, and expressions.

```
  Packet arrives at hook point
          │
          ▼
  ┌────────────────────────────────────────────────────────────────┐
  │  TABLE  (e.g. "filter", family = inet)                        │
  │  struct nft_table                                             │
  │                                                               │
  │  ┌────────────────────────────────────────────────────────┐   │
  │  │  BASE CHAIN  (e.g. "input", hook = NF_INET_INPUT)     │   │
  │  │  struct nft_chain  →  struct nft_base_chain            │   │
  │  │  type: filter | nat | route                            │   │
  │  │  priority: -200 … 300  (ordering among chains)         │   │
  │  │                                                        │   │
  │  │  ┌──────────────────────────────────────────────────┐  │   │
  │  │  │  RULE 1  (struct nft_rule)                       │  │   │
  │  │  │                                                  │  │   │
  │  │  │  Expression chain (left → right):                │  │   │
  │  │  │                                                  │  │   │
  │  │  │  ┌─────────┐  ┌──────────┐  ┌────────────────┐  │  │   │
  │  │  │  │ PAYLOAD │→│   CMP    │→│    VERDICT     │  │  │   │
  │  │  │  │ load    │  │ compare  │  │ accept / drop  │  │  │   │
  │  │  │  │ ip daddr│  │ == X     │  │ jump / goto    │  │  │   │
  │  │  │  └─────────┘  └──────────┘  └────────────────┘  │  │   │
  │  │  └──────────────────────────────────────────────────┘  │   │
  │  │                                                        │   │
  │  │  ┌──────────────────────────────────────────────────┐  │   │
  │  │  │  RULE 2  (struct nft_rule)                       │  │   │
  │  │  │                                                  │  │   │
  │  │  │  ┌─────────┐  ┌──────────┐  ┌─────┐  ┌──────┐  │  │   │
  │  │  │  │ META    │→│ LOOKUP   │→│ CTR │→│ LOG  │  │  │   │
  │  │  │  │ l4proto │  │ in set   │  │ cnt │  │ log  │  │  │   │
  │  │  │  └─────────┘  └──────────┘  └─────┘  └──────┘  │  │   │
  │  │  └──────────────────────────────────────────────────┘  │   │
  │  │                                                        │   │
  │  │  Policy: ACCEPT (default verdict if no rule matches)   │   │
  │  └────────────────────────────────────────────────────────┘   │
  │                                                               │
  │  ┌────────────────────────────────────────────────────────┐   │
  │  │  REGULAR CHAIN  (e.g. "my_chain", no hook)             │   │
  │  │  Called via jump/goto from base chains                  │   │
  │  └────────────────────────────────────────────────────────┘   │
  └────────────────────────────────────────────────────────────────┘

  nft_do_chain() walks each rule:
    for each expression in rule:
        expr->ops->eval(expr, regs, pkt)
        if regs->verdict != NFT_CONTINUE → stop rule
    if verdict is terminal → return to caller
    else → next rule
```

Key expression types (`struct nft_expr`):

| Expression       | Source file          | Purpose                          |
|------------------|----------------------|----------------------------------|
| `nft_payload`    | `nft_payload.c`      | Load packet header fields        |
| `nft_cmp`        | `nft_cmp.c`          | Compare register against value   |
| `nft_lookup`     | `nft_lookup.c`       | Set/map membership lookup        |
| `nft_immediate`  | `nft_immediate.c`    | Load immediate value / verdict   |
| `nft_meta`       | `nft_meta.c`         | Load packet metadata (iif, mark) |
| `nft_ct`         | `nft_ct.c`           | Load conntrack state/fields      |
| `nft_counter`    | `nft_counter.c`      | Packet/byte counters             |
| `nft_nat`        | `nft_nat.c`          | NAT (SNAT/DNAT)                  |
| `nft_log`        | `nft_log.c`          | Packet logging                   |
| `nft_limit`      | `nft_limit.c`        | Rate limiting                    |

---

## Connection Tracking State Machine

Conntrack (`nf_conntrack`) assigns every packet to a connection and tracks its
state transitions. This enables stateful firewalling rules like
`ct state established accept`.

```
                        First packet seen
                              │
                              ▼
                     ┌─────────────────┐
                     │                 │
                     │   IP_CT_NEW     │
                     │                 │
                     │  Tuple created  │
                     │  (src/dst/proto)│
                     │  Unconfirmed    │
                     └────────┬────────┘
                              │
                    Reply packet seen?
                     ┌────────┴────────┐
                     │ YES             │ NO (timeout)
                     ▼                 ▼
            ┌─────────────────┐   ┌──────────┐
            │                 │   │  DESTROY  │
            │ IP_CT_ESTABLISHED   │  (timeout │
            │                 │   │   or      │
            │  Both directions│   │  explicit │
            │  seen, confirmed│   │  delete)  │
            └────────┬────────┘   └──────────┘
                     │
          Related traffic (helper)?
          ┌──────────┴──────────┐
          │ YES                 │ NO
          ▼                     │
  ┌─────────────────┐          │
  │ IP_CT_RELATED   │          │
  │                 │          │
  │ Expectation     │          │
  │ matched (e.g.   │          │
  │ FTP data conn)  │          │
  │ → new conntrack │          │
  │   with RELATED  │          │
  │   status        │          │
  └─────────────────┘          │
                               │
          FIN/RST or timeout   │
          ┌────────────────────┘
          ▼
  ┌─────────────────┐
  │    DESTROY       │
  │                  │
  │  nf_ct_delete()  │
  │  Timer expired   │
  │  or TCP FIN/RST  │
  │  Entry removed   │
  │  from hash table │
  └──────────────────┘

  Conntrack entry lifecycle:
  ┌──────────┐    ┌───────────┐    ┌───────────┐    ┌──────────┐
  │ UNTRACKED│    │UNCONFIRMED│    │ CONFIRMED │    │ DYING    │
  │          │───►│           │───►│           │───►│          │
  │ raw/     │    │ allocated │    │ inserted  │    │ removed  │
  │ NOTRACK  │    │ not yet in│    │ in hash   │    │ from hash│
  │          │    │ hash table│    │ table     │    │ table    │
  └──────────┘    └───────────┘    └───────────┘    └──────────┘
```

---

## Key Data Structures

| Structure              | Header / Source                        | Purpose                                                      |
|------------------------|----------------------------------------|--------------------------------------------------------------|
| `struct nf_hook_state` | `include/linux/netfilter.h`            | Per-invocation hook context: hook num, protocol family, device, net namespace |
| `struct nf_hook_ops`   | `include/linux/netfilter.h`            | Registered hook function: callback, priority, hook number     |
| `struct nf_hook_entries`| `include/linux/netfilter.h`           | Array of hook functions registered at one hook point          |
| `struct nf_conn`       | `include/net/netfilter/nf_conntrack.h` | Connection tracking entry: tuples (orig/reply), status bits, timeout, NAT info, extensions |
| `struct nf_conntrack_tuple` | `include/net/netfilter/nf_conntrack_tuple.h` | 5-tuple (src IP, dst IP, src port, dst port, L4 proto) identifying a flow direction |
| `struct nf_conntrack_tuple_hash` | `include/net/netfilter/nf_conntrack_tuple.h` | Hash table node linking a tuple to its `nf_conn` |
| `struct nf_nat_range2` | `include/uapi/linux/netfilter/nf_nat.h` | NAT mapping specification: IP range, port range, flags       |
| `struct nft_table`     | `include/net/netfilter/nf_tables.h`    | nftables table: name, family, list of chains and sets         |
| `struct nft_chain`     | `include/net/netfilter/nf_tables.h`    | nftables chain: list of rules, use count, chain type          |
| `struct nft_base_chain`| `include/net/netfilter/nf_tables.h`    | Base chain extending nft_chain: hook registration, policy, stats |
| `struct nft_rule`      | `include/net/netfilter/nf_tables.h`    | nftables rule: variable-length array of expressions           |
| `struct nft_expr`      | `include/net/netfilter/nf_tables.h`    | nftables expression: ops pointer + private data               |
| `struct nft_regs`      | `include/net/netfilter/nf_tables.h`    | nftables register file: 16 × 32-bit registers + verdict       |
| `struct nft_pktinfo`   | `include/net/netfilter/nf_tables.h`    | nftables packet context: skb, hook state, transport offset    |
| `struct xt_table`      | `include/linux/netfilter/x_tables.h`   | iptables table (filter/nat/mangle/raw)                        |
| `struct ipt_entry`     | `include/uapi/linux/netfilter_ipv4/ip_tables.h` | iptables rule entry: match criteria + target       |

---

## Key Functions

| Function                   | Source                       | Purpose                                                       |
|----------------------------|------------------------------|---------------------------------------------------------------|
| `nf_hook_slow()`           | `net/netfilter/core.c`       | Walk the hook entry array, calling each registered hook function in priority order |
| `nf_hook()`                | `include/linux/netfilter.h`  | Inline fast-path: check if hooks exist, call `nf_hook_slow()` if so |
| `nf_register_net_hook()`   | `net/netfilter/core.c`       | Register a hook function at a specific hook point              |
| `nf_conntrack_in()`        | `net/netfilter/nf_conntrack_core.c` | Main conntrack entry point: resolve, lookup or create `nf_conn` for packet |
| `nf_conntrack_confirm()`   | `net/netfilter/nf_conntrack_core.c` | Insert unconfirmed conntrack into hash table at POSTROUTING/INPUT |
| `nf_conntrack_find_get()`  | `net/netfilter/nf_conntrack_core.c` | Look up a conntrack entry by tuple in the hash table           |
| `__nf_ct_refresh_acct()`   | `net/netfilter/nf_conntrack_core.c` | Refresh conntrack timeout and update byte/packet counters      |
| `nf_ct_delete()`           | `net/netfilter/nf_conntrack_core.c` | Mark a conntrack entry as dying and schedule removal           |
| `nf_nat_manip_pkt()`       | `net/netfilter/nf_nat_core.c`| Rewrite packet headers (IP, port) according to NAT mapping     |
| `nf_nat_setup_info()`      | `net/netfilter/nf_nat_core.c`| Initialize NAT mapping for a conntrack entry                   |
| `nft_do_chain()`           | `net/netfilter/nf_tables_core.c` | nftables chain evaluator: walk rules, execute expressions      |
| `nf_tables_newrule()`      | `net/netfilter/nf_tables_api.c` | Netlink handler to add a new rule to an nftables chain         |
| `ipt_do_table()`           | `net/netfilter/ip_tables.c`  | iptables rule evaluator: match entries and execute targets      |
| `nf_log_packet()`          | `net/netfilter/nf_log.c`    | Log a packet via the registered logging backend                |

---

## Practical Analogy

Think of Netfilter as an **airport security system** for network packets:

- **Hook points** are security checkpoints placed at fixed locations in the
  terminal (PREROUTING = arrival gate, INPUT = passport control, FORWARD =
  transit corridor, OUTPUT = departure gate, POSTROUTING = boarding gate).

- **iptables/nftables rules** are the security officers at each checkpoint,
  each with a specific task: check passports (match source IP), verify visas
  (match port/protocol), stamp documents (mangle TOS/TTL), redirect passengers
  (DNAT), or deny boarding (DROP).

- **Connection tracking** is the airline's passenger database. Once a
  traveller is checked in (NEW), the system remembers them for the return
  flight (ESTABLISHED). Family members travelling on a linked booking are
  RELATED connections.

- **NAT** is the gate-change announcement: the packet's "boarding pass"
  (source or destination address) is rewritten so it arrives at the correct
  gate, but conntrack remembers the original booking so replies find their
  way back.

- **nftables expressions** are a checklist of inspection steps applied in
  order — load the passport field, compare against the watchlist, increment
  the counter, log the result, issue the verdict.

---

## Further Reading

- `Documentation/networking/nf_conntrack-sysctl.rst` — conntrack tunables
- `nftables` wiki: https://wiki.nftables.org/
- `iptables` man page: `man 8 iptables`
- `conntrack-tools`: https://conntrack-tools.netfilter.org/
- Netfilter project: https://www.netfilter.org/
