# rfkill — RF Kill Switch Subsystem

## Overview

The **rfkill** subsystem provides a unified interface for controlling wireless
radio transmitters (WiFi, Bluetooth, WWAN, NFC, GPS, FM, etc.) on Linux.  Each
wireless device registers an `rfkill` object with the kernel; userspace can then
**soft-block** (disable via software) or query **hard-block** (physical kill
switch / firmware) state through a single character device (`/dev/rfkill`) or
through sysfs attributes under `/sys/class/rfkill/`.

The subsystem was introduced to replace a patchwork of vendor-specific kill
switch drivers with a single, consistent kernel ↔ userspace protocol.

---

## Architecture

```
 ┌──────────────────────────────────────────────────────────────────┐
 │                         USERSPACE                               │
 │                                                                 │
 │  rfkill list / block / unblock          NetworkManager / BlueZ  │
 │          │                                      │               │
 │          ▼                                      ▼               │
 │    ┌────────────┐                        ┌────────────┐         │
 │    │ /dev/rfkill │  (read / write /poll) │  sysfs     │         │
 │    │  char dev   │                       │ /sys/class/ │        │
 │    │  (misc 10)  │                       │  rfkill/*   │        │
 │    └─────┬──────┘                        └─────┬──────┘         │
 └──────────┼─────────────────────────────────────┼────────────────┘
            │           KERNEL                    │
            ▼                                     ▼
 ┌─────────────────────────────────────────────────────────────────┐
 │                      rfkill core                                │
 │                  (net/rfkill/core.c)                            │
 │                                                                 │
 │   • Maintains per-device soft/hard block state                  │
 │   • Broadcasts rfkill_event structs to all listeners            │
 │   • Drives driver callbacks on state change                     │
 │   • Handles global "all radios off" via /dev/rfkill             │
 └────────────────────────────┬────────────────────────────────────┘
                              │
          ┌───────────────────┼───────────────────┐
          ▼                   ▼                   ▼
 ┌───────────────┐   ┌───────────────┐   ┌───────────────┐
 │  WiFi driver  │   │   BT driver   │   │  WWAN driver  │
 │  (e.g. iwlwifi)│  │ (e.g. btusb)  │   │ (e.g. wwan)   │
 │               │   │               │   │               │
 │ .set_block()  │   │ .set_block()  │   │ .set_block()  │
 └───────────────┘   └───────────────┘   └───────────────┘
```

### Data-flow summary

1. **Userspace** writes an `rfkill_event` to `/dev/rfkill` (or echoes `1`/`0`
   to the sysfs `soft` attribute).
2. **rfkill core** validates the request, updates its internal state bitmap,
   then invokes the driver's `set_block()` callback.
3. The **driver** enables or disables the radio hardware and returns success or
   failure.
4. rfkill core **broadcasts** an `RFKILL_OP_CHANGE` event to every open
   `/dev/rfkill` file descriptor so that all listeners (NetworkManager, BlueZ,
   etc.) learn about the new state.

---

## Soft Block vs Hard Block

```
 ┌──────────────────────────────────────────────────────────────┐
 │                    Block-state matrix                        │
 ├──────────────┬──────────────┬────────────────────────────────┤
 │  soft_block  │  hard_block  │  Radio state                   │
 ├──────────────┼──────────────┼────────────────────────────────┤
 │      0       │      0       │  ON  — transmitting normally   │
 │      1       │      0       │  OFF — software disabled       │
 │      0       │      1       │  OFF — hardware switch off     │
 │      1       │      1       │  OFF — both blocks active      │
 └──────────────┴──────────────┴────────────────────────────────┘
```

| Property | Soft block | Hard block |
|----------|-----------|------------|
| Controlled by | Software (kernel / userspace) | Physical switch, firmware, BIOS |
| Toggled via | `rfkill block/unblock`, `/dev/rfkill` write, sysfs | Hardware button, ACPI hotkey |
| Kernel function | `rfkill_set_sw_state()` | `rfkill_set_hw_state()` |
| Can be overridden by software? | Yes | **No** — only the platform can clear it |

### rfkill Events

Every state change produces an `rfkill_event` (or the extended
`rfkill_event_ext`) that is delivered to every open `/dev/rfkill` fd.  Three
operation types exist:

| Operation | Value | Meaning |
|-----------|-------|---------|
| `RFKILL_OP_ADD` | 0 | New rfkill device registered |
| `RFKILL_OP_DEL` | 1 | rfkill device removed |
| `RFKILL_OP_CHANGE` | 2 | State changed (soft or hard) |
| `RFKILL_OP_CHANGE_ALL` | 3 | Change applied to all devices of a type |

---

## Key Structures

### `struct rfkill` (internal, `net/rfkill/core.c`)

```c
struct rfkill {
    const char          *name;       /* human-readable label           */
    enum rfkill_type     type;       /* RFKILL_TYPE_WLAN, _BLUETOOTH…  */
    unsigned long        state;      /* bitmask: RFKILL_BLOCK_SW / _HW */
    struct rfkill_ops   *ops;        /* driver callbacks                */
    void                *data;       /* driver private pointer          */
    struct device        dev;        /* sysfs device                    */
    struct list_head     node;       /* global rfkill list              */
    ...
};
```

### `struct rfkill_ops` (driver-facing)

```c
struct rfkill_ops {
    void (*poll)(struct rfkill *rfkill, void *data);
    void (*query)(struct rfkill *rfkill, void *data);
    int  (*set_block)(void *data, bool blocked);
};
```

| Callback | Purpose |
|----------|---------|
| `set_block` | **Required.** Turn radio on (`blocked=false`) or off (`blocked=true`). |
| `query` | Optional. Re-read current hw/sw state from firmware. |
| `poll` | Optional. Periodically polled by rfkill core to detect hw-switch changes. |

### `struct rfkill_event` (userspace ABI)

```c
struct rfkill_event {
    __u32 idx;       /* rfkill device index   */
    __u8  type;      /* enum rfkill_type      */
    __u8  op;        /* enum rfkill_operation  */
    __u8  soft;      /* 1 = soft-blocked       */
    __u8  hard;      /* 1 = hard-blocked       */
} __packed;
```

Userspace reads / writes these 8-byte structs through `/dev/rfkill`.

---

## Key Functions

| Function | Header | Purpose |
|----------|--------|---------|
| `rfkill_alloc(name, parent, type, ops, data)` | `<net/rfkill.h>` | Allocate a new rfkill object. Returns `struct rfkill *`. |
| `rfkill_register(rfkill)` | `<net/rfkill.h>` | Register with rfkill core; creates sysfs entries & sends `RFKILL_OP_ADD`. |
| `rfkill_unregister(rfkill)` | `<net/rfkill.h>` | Remove from core; sends `RFKILL_OP_DEL`. |
| `rfkill_destroy(rfkill)` | `<net/rfkill.h>` | Free memory (call after unregister). |
| `rfkill_set_sw_state(rfkill, blocked)` | `<net/rfkill.h>` | Driver reports current soft-block state to core. |
| `rfkill_set_hw_state(rfkill, blocked)` | `<net/rfkill.h>` | Driver reports hardware kill-switch state to core. |
| `rfkill_init_sw_state(rfkill, blocked)` | `<net/rfkill.h>` | Set initial soft state **before** `rfkill_register()`. |
| `rfkill_blocked(rfkill)` | `<net/rfkill.h>` | Returns `true` if device is blocked (soft OR hard). |

### Typical driver lifecycle

```
rfkill_alloc()          ← allocate
rfkill_init_sw_state()  ← set initial soft state (optional)
rfkill_register()       ← go live, sysfs appears
  ...
  rfkill_set_hw_state() ← report hardware-switch changes
  rfkill_set_sw_state() ← report firmware-driven soft changes
  ...
rfkill_unregister()     ← remove from core
rfkill_destroy()        ← free
```

---

## Common Operations

### Listing devices

```console
$ rfkill list
0: phy0: Wireless LAN
    Soft blocked: no
    Hard blocked: no
1: hci0: Bluetooth
    Soft blocked: yes
    Hard blocked: no
```

### Blocking / unblocking

```console
$ rfkill block wifi          # soft-block all WiFi radios
$ rfkill unblock bluetooth   # unblock all Bluetooth radios
$ rfkill block 0             # soft-block device index 0
$ rfkill unblock all         # unblock everything
```

### Reading events from `/dev/rfkill`

```console
$ cat /dev/rfkill | xxd      # raw 8-byte rfkill_event structs
```

### sysfs interface

```
/sys/class/rfkill/rfkill0/
├── name            # e.g. "phy0"
├── type            # e.g. "wlan"
├── state           # 0 = blocked, 1 = unblocked (legacy)
├── soft            # 0 / 1
├── hard            # 0 / 1
└── device -> …     # link to parent device
```

---

## Key Source Files

| File | Description |
|------|-------------|
| `net/rfkill/core.c` | Core logic: registration, state machine, `/dev/rfkill` fops, sysfs |
| `net/rfkill/input.c` | Input-layer handler for KEY_RFKILL / KEY_WLAN / KEY_BLUETOOTH |
| `include/linux/rfkill.h` | In-kernel API (`rfkill_alloc`, `rfkill_register`, …) |
| `include/uapi/linux/rfkill.h` | Userspace ABI (`rfkill_event`, enums) |
| `Documentation/driver-api/rfkill.rst` | Official kernel documentation |

---

## Analogy

Think of rfkill as a **building-wide fire alarm panel** for radio transmitters.

- Each wireless radio is a **zone** on the panel (WiFi = zone 0, Bluetooth =
  zone 1, …).
- The **soft block** is like a software-controlled relay: the building manager
  (userspace) can flip it from the control room.
- The **hard block** is like a physical pull station: once someone pulls the
  handle (hardware kill switch), the control room cannot override it — only
  physically resetting the pull station restores service.
- The `/dev/rfkill` device is the panel's **event bus**: every monitoring
  station (NetworkManager, BlueZ) keeps a connection open and receives real-time
  updates whenever any zone changes state.

---

## References

- `Documentation/driver-api/rfkill.rst` — upstream kernel docs
- `include/linux/rfkill.h` — kernel API header
- `include/uapi/linux/rfkill.h` — userspace ABI header
- `net/rfkill/core.c` — authoritative implementation
- `man 8 rfkill` — userspace tool manual
- [kernel.org rfkill docs](https://www.kernel.org/doc/html/latest/driver-api/rfkill.html)
