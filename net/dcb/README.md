# Linux Kernel net/dcb — Data Center Bridging

## Overview

**DCB** (Data Center Bridging) implements the IEEE 802.1Qaz framework in the
Linux kernel for lossless Ethernet in data center environments. It provides
**Priority-based Flow Control (PFC)** to prevent packet loss on specific
traffic classes, **Enhanced Transmission Selection (ETS)** for bandwidth
allocation, and **DCBX** protocol negotiation with link partners. The DCB
subsystem uses rtnetlink to expose configuration to userspace tools like
`lldptool` and `dcbtool`.

Source: `net/dcb/`, `include/net/dcbnl.h`.

---

## Subsystem Stack

```
┌─────────────────────────────────────────────────────────────────┐
│                        USERSPACE                                │
│                                                                 │
│  lldptool      dcbtool         tc (traffic control)             │
│  DCB-capable app configuration  RoCE / FCoE apps               │
└───────────────────────────────┬─────────────────────────────────┘
                                │ rtnetlink (DCB_CMD_*)
┌───────────────────────────────▼─────────────────────────────────┐
│                   DCB NETLINK LAYER                              │
│                   (net/dcb/dcbnl.c)                              │
│                                                                 │
│  dcbnl_notify()     — send DCB change notifications            │
│  dcbnl_ieee_set()   — set IEEE 802.1Qaz parameters             │
│  dcbnl_ieee_get()   — get IEEE 802.1Qaz parameters             │
│                                                                 │
│  Commands: DCB_CMD_IEEE_SET, DCB_CMD_IEEE_GET,                  │
│            DCB_CMD_PFC_GCFG, DCB_CMD_GDCBX, …                 │
└───────────────────────────────┬─────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────┐
│                   DCB APPLICATION PRIORITY                       │
│                   (net/dcb/dcbnl.c)                              │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  struct dcb_app  (application priority entry)           │   │
│  │  - selector   (L2/L4 port type)                         │   │
│  │  - protocol   (port number / ethertype)                 │   │
│  │  - priority   (traffic class 0-7)                       │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  dcb_setapp()  — set application priority mapping              │
│  dcb_getapp()  — get application priority mapping              │
└───────────────────────────────┬─────────────────────────────────┘
                                │ dcbnl_rtnl_ops callbacks
┌───────────────────────────────▼─────────────────────────────────┐
│                   NIC DRIVERS (DCB-capable)                      │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  struct dcbnl_rtnl_ops  (driver DCB callbacks)          │   │
│  │  - ieee_setets()    set ETS bandwidth allocation        │   │
│  │  - ieee_setpfc()    set PFC configuration               │   │
│  │  - getdcbx()        get DCBX mode                       │   │
│  │  - setdcbx()        set DCBX mode                       │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  mlx5, ice, bnxt, i40e, qede — DCB-capable drivers            │
└───────────────────────────────┬─────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────┐
│               NIC HARDWARE (PFC / ETS / DCBX offload)           │
└─────────────────────────────────────────────────────────────────┘
```

---

## Workflow: Setting DCB Application Priority

```
  lldptool -T -i eth0 -V APP ...
       │
       ▼
  rtnetlink DCB_CMD_IEEE_SET
       │
       ▼
  dcbnl_ieee_set()
       │
       ├──► dcb_setapp(dev, &app)
       │         │
       │         ├──► add to per-device app list
       │         └──► call_dcbx_cb()   notify DCBX
       │
       ├──► dev->dcbnl_ops->ieee_setets()   configure ETS
       ├──► dev->dcbnl_ops->ieee_setpfc()   configure PFC
       └──► dcbnl_notify()                  broadcast change
```

---

## Key Data Structures

| Structure | Purpose |
|---|---|
| `struct dcb_app` | Application-to-priority mapping entry |
| `struct dcbnl_rtnl_ops` | Driver callbacks for DCB configuration |
| `struct ieee_ets` | ETS bandwidth allocation per traffic class |
| `struct ieee_pfc` | PFC enable/disable per priority |
| `struct dcb_ieee_app_dscp_map` | DSCP-to-priority mapping |

## Key Functions

| Function | Purpose |
|---|---|
| `dcb_setapp()` | Set application priority mapping |
| `dcb_getapp()` | Get application priority for protocol |
| `dcbnl_notify()` | Broadcast DCB configuration change |
| `dcbnl_ieee_set()` | Handle IEEE DCB set command from userspace |
| `dcbnl_ieee_get()` | Handle IEEE DCB get command from userspace |
| `dcb_ieee_setapp()` | Set IEEE application priority entry |

## Key Source Files

| File | Purpose |
|---|---|
| `net/dcb/dcbnl.c` | DCB netlink interface and app priority |
| `include/net/dcbnl.h` | DCB netlink API |
| `include/uapi/linux/dcbnl.h` | Userspace ABI |
| `include/linux/dcbnl.h` | Kernel DCB structures |

---

## Analogy

DCB is like a **hospital emergency room triage system for network traffic**:

- **Traffic classes** (0-7) are like triage levels — critical (RoCE storage)
  gets priority over routine (web browsing).
- **PFC** (Priority-based Flow Control) is the "stop admitting" signal — when
  a particular priority queue fills up, PFC tells the sender to pause, ensuring
  zero packet loss for that class.
- **ETS** (Enhanced Transmission Selection) is the budget allocation — "60% of
  bandwidth to storage, 30% to compute, 10% to management."
- **DCBX** is the negotiation protocol — both ends of the link agree on the
  triage rules before traffic flows.

---

## References

- `include/net/dcbnl.h` — DCB netlink API
- `include/uapi/linux/dcbnl.h` — Userspace ABI
- `Documentation/networking/dcb/` — DCB documentation
- `net/dcb/` — Implementation
