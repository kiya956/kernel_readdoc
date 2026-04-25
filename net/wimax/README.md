# WiMAX Networking Subsystem (`net/wimax`)

## Overview

WiMAX (Worldwide Interoperability for Microwave Access, IEEE 802.16) is a
broadband wireless networking standard. The Linux kernel's WiMAX subsystem
provides a generic framework for WiMAX device drivers, offering device
lifecycle management, RF state control, and a genetlink-based control channel
for userspace management tools.

**Note:** WiMAX hardware has been largely superseded by LTE/5G. The kernel
subsystem is maintained but sees minimal active development. The primary
in-tree driver was Intel's i2400m (now removed), leaving the framework as
a reference for any remaining or out-of-tree WiMAX devices.

## Architecture

```
┌─────────────────────────────────────────────────────┐
│           Userspace (wimaxll / NetworkManager)       │
│              (genetlink control interface)            │
├─────────────────────────────────────────────────────┤
│                  WiMAX Core Framework                │
│  ┌────────────────────────────────────────────────┐  │
│  │             struct wimax_dev                    │  │
│  │  ┌──────────────┐  ┌──────────────────────┐    │  │
│  │  │  RF control   │  │  Device state machine│    │  │
│  │  │  (on/off)     │  │  (init→ready→down)   │    │  │
│  │  └──────────────┘  └──────────────────────┘    │  │
│  │  ┌──────────────────────────────────────────┐  │  │
│  │  │  Generic Netlink (genetlink) interface   │  │  │
│  │  │  (msg send/recv, state report)           │  │  │
│  │  └──────────────────────────────────────────┘  │  │
│  └────────────────────────────────────────────────┘  │
├─────────────────────────────────────────────────────┤
│              WiMAX Device Driver                     │
│         (hardware-specific operations)               │
├─────────────────────────────────────────────────────┤
│              Network Device (net_device)              │
│              (standard Linux netdev)                  │
└─────────────────────────────────────────────────────┘
```

## Device Lifecycle Workflow

```
  Driver                    WiMAX Core              Userspace
    │                          │                       │
    │  wimax_dev_init()        │                       │
    │─────────────────────────►│                       │
    │                          │                       │
    │  wimax_dev_add()         │                       │
    │─────────────────────────►│                       │
    │                          │── genetlink register──►│
    │                          │                       │
    │                          │◄── RF ON command ─────│
    │◄── op_rfkill_sw_toggle ──│                       │
    │                          │                       │
    │  wimax_state_change()    │                       │
    │─────────────────────────►│                       │
    │                          │── state notification──►│
    │                          │                       │
    │  wimax_msg()             │                       │
    │─────────────────────────►│                       │
    │                          │── driver message ─────►│
    │                          │                       │
    │  wimax_dev_rm()          │                       │
    │─────────────────────────►│                       │
    │                          │── genetlink unregister►│
```

## Key Structures

| Structure          | Description                                              |
|--------------------|----------------------------------------------------------|
| `struct wimax_dev` | WiMAX device — state, RF control, genetlink family, ops  |

## Key Functions

| Function                  | Description                                      |
|---------------------------|--------------------------------------------------|
| `wimax_dev_init()`        | Initialize wimax_dev structure                   |
| `wimax_dev_add()`         | Register WiMAX device with framework + genetlink |
| `wimax_dev_rm()`          | Unregister and clean up WiMAX device             |
| `wimax_msg()`             | Send a driver message to userspace via genetlink  |
| `wimax_msg_send()`        | Transmit a pre-built genetlink message            |
| `wimax_state_change()`    | Report device state change to core                |
| `wimax_rfkill()`          | Control RF state (on/off/query)                   |
| `wimax_reset()`           | Reset the WiMAX device                            |

## Analogy

The WiMAX subsystem is like a **TV remote control framework**. The
`wimax_dev` is a universal remote (framework) that works with any TV brand
(driver). It has standard buttons: power on/off (`wimax_rfkill`), reset
(`wimax_reset`), and a display showing the TV's state (`wimax_state_change`).
The genetlink interface is the infrared beam — carrying commands to the TV
and status messages back. Even if the TV brand changes, the remote's buttons
work the same way.

## Source Files

| File                    | Purpose                              |
|-------------------------|--------------------------------------|
| `net/wimax/wimax-internal.h` | Internal declarations            |
| `net/wimax/stack.c`     | Core device lifecycle (add/rm/init)  |
| `net/wimax/op-msg.c`    | Message passing operations           |
| `net/wimax/op-rfkill.c` | RF kill switch operations            |
| `net/wimax/op-reset.c`  | Device reset operations              |
| `net/wimax/op-state-get.c` | State query operations            |
| `net/wimax/id-table.c`  | Device ID table management           |
