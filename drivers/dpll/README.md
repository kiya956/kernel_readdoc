# Linux Kernel: DPLL (Digital Phase-Locked Loop) Subsystem

> Source: `drivers/dpll/` — noble-linux-oem (oem-6.17-next)

---

## 1. What is DPLL?

A **Digital Phase-Locked Loop** is a hardware circuit that locks its output
clock to an input reference signal with very high stability and low jitter.
Used in:

- **Telecom/networking** — SyncE (Synchronous Ethernet), IEEE 1588 PTP
  hardware timestamping, SONET/SDH network synchronization
- **Industrial** — motion controllers, precise timing buses
- **Test equipment** — signal generators, frequency standards

The Linux DPLL subsystem (introduced in kernel 6.2) exposes DPLL devices
as Netlink objects, allowing `dpll` tooling to monitor lock state, select
input pins, and receive asynchronous state change notifications.

---

## 2. Subsystem Stack

```
┌──────────────────────────────────────────────────────────────────┐
│                    USERSPACE                                     │
│  dpll (iproute2 tool)  /  custom netlink apps                   │
│  dpll dev get  dpll pin get  dpll mon                           │
└───────────────────────┬──────────────────────────────────────────┘
                        │  Netlink (DPLL_GENL_NAME family)
                        ▼
┌──────────────────────────────────────────────────────────────────┐
│           DPLL NETLINK  (dpll_netlink.c + dpll_nl.c)            │
│                                                                  │
│  DPLL_CMD_DEV_GET     — enumerate DPLL devices                  │
│  DPLL_CMD_PIN_GET     — enumerate pins + state                  │
│  DPLL_CMD_PIN_SET     — set pin priority / state / frequency    │
│  Async notifications  — DPLL_CMD_DEV_CHANGE_NTF                 │
│                         DPLL_CMD_PIN_CHANGE_NTF                  │
└──────────────────────┬───────────────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│               DPLL CORE  (dpll_core.c)                          │
│                                                                  │
│  dpll_device_alloc()      — allocate dpll_device object         │
│  dpll_device_register()   — register with subsystem             │
│  dpll_pin_alloc()         — allocate pin                        │
│  dpll_pin_register()      — connect pin to dpll_device          │
│  dpll_device_notify()     — fire Netlink notification on change  │
│  dpll_pin_notify()        — fire pin state change notification   │
│                                                                  │
│  XArray dpll_device_xa   — all registered DPLL devices          │
│  XArray dpll_pin_xa      — all registered pins                  │
└──────────────────────┬───────────────────────────────────────────┘
                       │  dpll_device_ops + dpll_pin_ops callbacks
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│              HARDWARE DRIVERS                                    │
│                                                                  │
│  zl3073x/  — Renesas ZL30733 / ZL30735 DPLL chips               │
│               (SPI/I2C, SyncE + PTP clock source)               │
│                                                                  │
│  NIC-integrated DPLLs (registered via their network drivers):    │
│  ice.ko    — Intel E810 (SyncE / Telecom profile DPLL)          │
│  idpf.ko   — Intel infrastructure DPLL                          │
│                                                                  │
│  Other users (out-of-tree or upcoming):                         │
│  Renesas FemtoClock / ClockMatrix (via ptp_clockmatrix)         │
│  Microchip VSC / LAN966x                                        │
└──────────────────────┬───────────────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│  DPLL chip hardware / NIC on-chip DPLL                          │
│  Input pins: SyncE recovered clock, GNSS 1PPS, external 10MHz   │
│  Output: frequency reference for PHC / NIC TX clock             │
└──────────────────────────────────────────────────────────────────┘
```

---

## 3. Components

### 3.1 `dpll_core.c` — Object Model

Everything is an **xarray-indexed object**:

```c
/* Driver: register a DPLL device */
dpll = dpll_device_alloc(clock_id, dev_driver_id, THIS_MODULE);
dpll_device_register(dpll, DPLL_TYPE_EEC, &my_dpll_ops, priv);

/* Register an input pin */
pin = dpll_pin_alloc(clock_id, pin_board_label, THIS_MODULE,
                     &pin_prop);   // freq_supported[], capabilities
dpll_pin_register(dpll, pin, &my_pin_ops, priv);

/* Notify on lock status change (called from IRQ/work) */
dpll_device_notify(dpll, DPLL_A_LOCK_STATUS);
```

**dpll_device_ops** callbacks:
| Callback | Purpose |
|---|---|
| `mode_get` | Return MANUAL or AUTOMATIC |
| `mode_set` | Switch selection mode |
| `lock_status_get` | UNLOCKED / LOCKED / LOCKED_HO_ACQ / HOLDOVER |
| `temp_get` | Temperature of DPLL chip (optional) |
| `phase_offset_get` | Current phase offset in ps |

**dpll_pin_ops** callbacks:
| Callback | Purpose |
|---|---|
| `frequency_get/set` | Get/set expected pin frequency |
| `direction_get/set` | INPUT or OUTPUT |
| `prio_get/set` | Priority for AUTOMATIC mode selection |
| `state_on_dpll_get/set` | CONNECTED / DISCONNECTED / SELECTABLE |
| `phase_adjust_get/set` | Fine phase trim |
| `ffo_get` | Fractional frequency offset (ppb × 2^10) |

### 3.2 `dpll_netlink.c` — Netlink Family

Uses **Generic Netlink** (`DPLL_GENL_NAME = "dpll"`).

Key message flows:

**Dump all devices:**
```
→ DPLL_CMD_DEV_GET (NLM_F_DUMP)
← DPLL_CMD_DEV_GET reply: clock_id, module_name, type, lock_status, mode
```

**Monitor changes:**
```
→ subscribe to DPLL_MCGRP_MONITOR multicast group
← DPLL_CMD_DEV_CHANGE_NTF when lock_status changes
← DPLL_CMD_PIN_CHANGE_NTF when pin state/prio changes
```

**Select a pin:**
```
→ DPLL_CMD_PIN_SET: pin_id, DPLL_A_PIN_STATE=SELECTABLE
```

### 3.3 Lock Status State Machine

```
                   valid reference available
UNLOCKED ──────────────────────────────────► LOCKED
   ▲                                             │
   │  reference lost                             │ holdover acquired
   │  (no holdover capable)                      ▼
   │                                    LOCKED_HO_ACQ
   │                                             │
   │  reference lost                             │ reference lost
   │  (after holdover)         ◄────────────────
   └───────────────────── HOLDOVER
          (free-running on internal oscillator)
```

---

## 4. Pin Types and Signal Sources

| `DPLL_PIN_TYPE_*` | Source |
|---|---|
| `MUX` | Mux of other pins |
| `EXT` | External SMA/BNC connector |
| `SYNCE_ETH_PORT` | SyncE recovered clock from Ethernet PHY |
| `INT_OSCILLATOR` | Internal TCXO/OCXO |
| `GNSS` | GPS/GNSS 1PPS input |

---

## 5. DPLL in a Telecom Network Switch

```
 Uplink SyncE ──► SYNCE_ETH_PORT pin
                        │ recovered 125 MHz
                        ▼
              ┌─────────────────┐
              │   DPLL chip     │  mode=AUTOMATIC (highest prio locked)
              │  (ZL30733)      │
              └────────┬────────┘
                       │
         ┌─────────────┼─────────────┐
         ▼             ▼             ▼
   NIC TX clock   PHC reference   SyncE out
   (line rate)    (IEEE 1588)     (to downstream)
```

---

## 6. Userspace Usage

```bash
# List DPLL devices
dpll dev get

# List pins on a device
dpll pin get

# Monitor state changes
dpll mon

# Select a specific pin (manual mode)
dpll pin set id <pin_id> parent-device id <dpll_id> state selectable
```

---

## 7. Summary

The DPLL subsystem:
1. **Netlink-native** — zero char devices or sysfs files; all management
   via Generic Netlink, consistent with modern kernel subsystems (ethtool,
   nl80211, devlink).
2. **Event-driven** — drivers call `dpll_device_notify()` on state change;
   monitoring tools receive async multicast notifications.
3. **Composable pin model** — each signal source is an independently
   registered pin, enabling complex DPLL trees (mux pins, cascaded DPLLs).
4. **Tight integration with PTP/SyncE** — Intel E810 registers both a
   `ptp_clock` and a `dpll_device` so `ptp4l` and `dpll mon` together
   provide complete telecom timing visibility.
