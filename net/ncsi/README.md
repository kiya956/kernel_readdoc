# NCSI — Network Controller Sideband Interface

## Overview

The NC-SI (Network Controller Sideband Interface) subsystem implements the DMTF
DSP0222 standard for out-of-band management communication between a Baseboard
Management Controller (BMC) and network interface controllers (NICs). NC-SI enables
the BMC to share the host's network connection without a dedicated management port.

Key capabilities:

- **Channel discovery** — Enumerate NICs and their channels
- **Configuration** — Set MAC filters, VLAN, enable/disable channels
- **Pass-through** — Share host NIC for BMC management traffic
- **Hardware arbitration** — Multiple packages and channels per NIC

NC-SI is widely used in server/data-center BMC firmware (OpenBMC) for IPMI-over-LAN,
Redfish, and other management protocols.

## Kernel Source

- **Directory:** `net/ncsi/`
- **Headers:** `include/net/ncsi.h`
- **Config:** `CONFIG_NET_NCSI`

## Architecture

```
┌─────────────────────────────────────────────┐
│           BMC User Space                    │
│      (OpenBMC, ipmitool, Redfish)          │
├─────────────────────────────────────────────┤
│         BMC Network Stack                   │
│      (eth0 shared via NC-SI)               │
├─────────────────────────────────────────────┤
│            NC-SI Manager                    │
│   ┌──────────────────────────────────┐      │
│   │  struct ncsi_dev_priv            │      │
│   │  ┌────────────────────────┐      │      │
│   │  │ Package 0              │      │      │
│   │  │  ├─ Channel 0 (active) │      │      │
│   │  │  ├─ Channel 1          │      │      │
│   │  │  └─ Channel N          │      │      │
│   │  ├────────────────────────┤      │      │
│   │  │ Package 1              │      │      │
│   │  │  └─ Channel 0          │      │      │
│   │  └────────────────────────┘      │      │
│   └──────────────────────────────────┘      │
├─────────────────────────────────────────────┤
│       NC-SI Command/Response Layer          │
│    AEN handling, state machine              │
├─────────────────────────────────────────────┤
│     Network Device Driver (ftgmac100, etc.) │
│     NC-SI sideband channel on same NIC      │
└─────────────────────────────────────────────┘
```

## Workflow

```
 BMC BOOT / NIC DETECTION                RUNTIME EVENT (AEN)
 ─────────────────────                   ───────────────────

 ncsi_start_dev()                        NIC sends AEN frame
     │                                        │
     ▼                                        ▼
 ncsi_probe_channel()                    ncsi_rcv_rsp()
     │                                        │
     ▼                                        ▼
 Send DESELECT_PACKAGE                   Parse AEN type
 Send SELECT_PACKAGE                     (Link change, Config)
     │                                        │
     ▼                                        ▼
 Send CLEAR_INITIAL_STATE                ncsi_aen_handler_lsc()
     │                                   (Link Status Change)
     ▼                                        │
 Send GET_CAPABILITIES                        ▼
 Send SET_MAC_ADDRESS                    Re-configure channel
 Send ENABLE_CHANNEL                     ncsi_configure_channel()
     │                                        │
     ▼                                        ▼
 Channel active, BMC online              Channel reconfigured
```

## Key Structures

| Structure | File | Purpose |
|-----------|------|---------|
| `struct ncsi_dev` | `include/net/ncsi.h` | Public NC-SI device handle |
| `struct ncsi_dev_priv` | `net/ncsi/internal.h` | Private device state, package list |
| `struct ncsi_package` | `net/ncsi/internal.h` | NC-SI package (physical NIC) |
| `struct ncsi_channel` | `net/ncsi/internal.h` | NC-SI channel within a package |
| `struct ncsi_request` | `net/ncsi/internal.h` | Pending command/response pair |

## Key Functions

| Function | File | Purpose |
|----------|------|---------|
| `ncsi_rcv_rsp()` | `net/ncsi/ncsi-rsp.c` | Handle NC-SI response/AEN frames |
| `ncsi_start_dev()` | `net/ncsi/ncsi-manage.c` | Start NC-SI on a network device |
| `ncsi_stop_dev()` | `net/ncsi/ncsi-manage.c` | Stop NC-SI management |
| `ncsi_configure_channel()` | `net/ncsi/ncsi-manage.c` | Configure a channel (MAC, VLAN, etc.) |
| `ncsi_send_cmd()` | `net/ncsi/ncsi-cmd.c` | Build and send NC-SI command frame |
| `ncsi_aen_handler_lsc()` | `net/ncsi/ncsi-aen.c` | Handle Link Status Change AEN |

## Analogy

NC-SI is like a **building's intercom system shared with the security office**. The
building (server) has a main phone line (NIC). The security office (BMC) doesn't
have its own phone line — instead, it uses the building's line via a sideband
channel. NC-SI negotiates which phone line to share, sets up the caller ID (MAC
filter), and handles events like "the phone line went down" (link status change).
The security office can monitor and manage the building without its own connection.
