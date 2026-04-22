# Linux Kernel extcon (External Connector) Subsystem

## Overview

The **extcon** (External Connector) framework provides a unified kernel API for
managing physical connector state changes — USB plugging, headphone insertion,
HDMI connection, USB-C mode negotiation, and more. It decouples the
*provider* (the driver that detects hardware events) from the *consumer* (the
driver that reacts to them) through a sysfs class and a kernel notifier chain.

Source location: `drivers/extcon/extcon.c`, `include/linux/extcon.h`,
`include/linux/extcon-provider.h`.

---

## Subsystem Stack

```
┌─────────────────────────────────────────────────────────────────┐
│                        USERSPACE                                │
│                                                                 │
│  udev rules / systemd / PulseAudio / ModemManager              │
│       │                          │                             │
│  /sys/class/extcon/<name>/       uevent (KOBJ_CHANGE)          │
│       cable.X/state              on state transition           │
│       name                                                     │
└────────────────────────────────┬────────────────────────────────┘
                                 │ sysfs / uevent boundary
┌────────────────────────────────▼────────────────────────────────┐
│                  EXTCON CORE  (drivers/extcon/extcon.c)         │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  extcon_dev  (one per physical connector hub)            │  │
│  │                                                          │  │
│  │  name  ──  supported_cable[]  ──  state[]                │  │
│  │                                   (per-cable bitmask)    │  │
│  │  prop[]  (USB: speed/vbus; DISP: hpd/colour-depth; …)    │  │
│  │                                                          │  │
│  │  nh[]  ──  notifier_head per cable ID                    │  │
│  │  nh_all  ──  notifier_head for all cables                │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                 │
│  Global lookup:  extcon_dev_list  ─── extcon_get_extcon_dev()   │
│  OF lookup:      extcon_get_edev_by_phandle()                   │
│  Sysfs class:    /sys/class/extcon/                             │
└──────────────────────┬─────────────────────┬────────────────────┘
                       │                     │
        ┌──────────────▼──────┐   ┌──────────▼──────────────────┐
        │  PROVIDER DRIVERS   │   │  CONSUMER DRIVERS           │
        │  (detect connectors)│   │  (react to events)          │
        │                     │   │                             │
        │ extcon-gpio.c       │   │  USB subsystem              │
        │ extcon-axp288.c     │   │  (dwc3, musb, xhci)         │
        │ extcon-intel-*.c    │   │                             │
        │ extcon-max14577.c   │   │  Charger / power_supply     │
        │ extcon-ptn5150.c    │   │                             │
        │   (USB-C port, I2C) │   │  Display / HDMI subsystem   │
        │ extcon-usbc-*.c     │   │                             │
        │ extcon-fsa9480.c    │   │  ASoC / audio jack          │
        │   (combo jack, ADC) │   │                             │
        │ extcon-adc-jack.c   │   │  extcon_register_notifier() │
        └─────────────────────┘   └─────────────────────────────┘
                       │
        ┌──────────────▼──────────────────────────────────────────┐
        │                    HARDWARE                             │
        │  GPIO pins / I2C chips / PMIC / USB-C PD controller     │
        │  Combo jack ADC / Intel CHT Whiskey Cove PMIC           │
        └─────────────────────────────────────────────────────────┘
```

---

## Layer-by-Layer Explanation

### 1. Userspace Interface

The extcon core registers a `struct class` called `extcon`. Each registered
`extcon_dev` appears under `/sys/class/extcon/<name>/`:

```
/sys/class/extcon/max77693-muic/
├── name          ← "max77693-muic"
├── state         ← bitmask of all active cables
├── cable.0/      ← per-cable directory (EXTCON_USB)
│   ├── name      ← "USB"
│   └── state     ← "1" (connected) or "0"
├── cable.1/      ← EXTCON_USB_HOST
...
```

When a cable state changes, a `uevent` (`KOBJ_CHANGE`) is emitted so that
userspace tools (udev, systemd, PulseAudio) can react without polling.

### 2. extcon_dev — The Core Object

`struct extcon_dev` represents one physical connector hub (e.g., a USB-C port,
a combo headphone/mic jack, or a PMIC's USB switch):

| Field | Purpose |
|---|---|
| `name` | sysfs directory name |
| `supported_cable[]` | NULL-terminated array of `EXTCON_*` IDs this device can report |
| `state` | Current connected bitmask (bit N = cable N is active) |
| `props[id][prop]` | Per-cable properties (speed, orientation, HPD, etc.) |
| `nh[id]` | Per-cable `atomic_notifier_head` |
| `nh_all` | `atomic_notifier_head` for any cable change |

### 3. Cable IDs (`EXTCON_*`)

Defined in `include/linux/extcon.h`. Grouped by type:

| Type | Examples |
|---|---|
| `EXTCON_TYPE_USB` | `EXTCON_USB` (1), `EXTCON_USB_HOST` (2) |
| `EXTCON_TYPE_CHG` | `EXTCON_CHG_USB_SDP` (5), `EXTCON_CHG_USB_DCP` (6), `EXTCON_CHG_USB_PD` (12) |
| `EXTCON_TYPE_JACK` | `EXTCON_JACK_MICROPHONE` (20), `EXTCON_JACK_HEADPHONE` (21) |
| `EXTCON_TYPE_DISP` | `EXTCON_DISP_HDMI` (40), `EXTCON_DISP_DP` (44) |
| `EXTCON_TYPE_MISC` | `EXTCON_MECHANICAL` (50), `EXTCON_DOCK` (52) |

### 4. Provider Driver API (`extcon-provider.h`)

A provider driver (e.g., `extcon-gpio.c`, `extcon-ptn5150.c`) follows these steps:

```c
// 1. Allocate
edev = devm_extcon_dev_allocate(dev, supported_cables);

// 2. Register
devm_extcon_dev_register(dev, edev);

// 3. Report state change (e.g., USB plugged in)
extcon_set_state_sync(edev, EXTCON_USB, true);

// Optional: set a property (e.g., USB speed)
extcon_set_property(edev, EXTCON_USB, EXTCON_PROP_USB_SS, (union extcon_property_value){ .intval = 1 });
extcon_sync(edev, EXTCON_USB);
```

### 5. Consumer Driver API (`extcon.h`)

A consumer driver (e.g., a USB controller driver) reacts to events:

```c
// Get the extcon device (by name or DT phandle)
edev = extcon_get_edev_by_phandle(dev, 0);

// Register a notifier for USB state changes
extcon_register_notifier(edev, EXTCON_USB, &nb);

// In the notifier callback:
int usb_notifier(struct notifier_block *nb, unsigned long event, void *ptr)
{
    bool connected = (event == 1);
    // enable/disable USB controller
    return NOTIFY_OK;
}
```

### 6. Notable Provider Drivers

| Driver | Hardware | Cables detected |
|---|---|---|
| `extcon-gpio.c` | Single GPIO line | Any one cable |
| `extcon-axp288.c` | X-Power AXP288 PMIC | USB, charger types |
| `extcon-intel-int3496.c` | Intel Cherrytrail ID pin | USB, USB-HOST |
| `extcon-intel-cht-wc.c` | Whiskey Cove PMIC | USB, SDP/DCP/CDP |
| `extcon-ptn5150.c` | NXP PTN5150 USB-C, I2C | USB, USB-HOST, USB-C orientation |
| `extcon-usbc-cros-ec.c` | ChromeOS EC USB-C | USB, USB-HOST, DP |
| `extcon-usbc-tusb320.c` | TI TUSB320 USB-C, I2C | USB, USB-HOST |
| `extcon-max77693.c` | Maxim MAX77693 MUIC | USB, charger, OTG |
| `extcon-adc-jack.c` | ADC-based combo jack | Headphone, Mic, Line |
| `extcon-fsa9480.c` | Fairchild FSA9480 USB switch | USB, charger, OTG |

---

## Connector State Change Flow

```
Hardware Event              Provider Driver             extcon Core
      │                           │                         │
      │  GPIO IRQ / I2C IRQ        │                         │
      │ ─────────────────────────►│                         │
      │                           │  extcon_set_state_sync( │
      │                           │    edev, EXTCON_USB,    │
      │                           │    true)                │
      │                           │ ───────────────────────►│
      │                           │                         │ update state[]
      │                           │                         │ atomic_notifier_call_chain(nh[USB])
      │                           │                         │
      │                           │                         │──────────────────────────────┐
      │                           │                         │   Consumer notifier callback │
      │                           │                         │   (USB controller, charger)  │
      │                           │                         │◄─────────────────────────────┘
      │                           │                         │
      │                           │                         │ kobject_uevent(KOBJ_CHANGE)
      │                           │                         │──► udev / systemd
      │                           │                         │
      │                           │                         │ sysfs state updated:
      │                           │                         │ /sys/class/extcon/.../cable.N/state = 1
```

---

## Key Source Files

| File | Role |
|---|---|
| `drivers/extcon/extcon.c` | Core: device lifecycle, state machine, notifier dispatch, sysfs |
| `drivers/extcon/devres.c` | Managed resource wrappers (`devm_*`) |
| `include/linux/extcon.h` | Consumer API + cable ID definitions |
| `include/linux/extcon-provider.h` | Provider API |
| `drivers/extcon/extcon-gpio.c` | Simplest GPIO-based provider |
| `drivers/extcon/extcon-ptn5150.c` | USB-C (PTN5150, I2C) provider |
| `drivers/extcon/extcon-axp288.c` | Intel Bay/Cherry Trail PMIC provider |
| `drivers/extcon/extcon-usbc-cros-ec.c` | ChromeOS EC USB-C provider |

---

## Analogy

Think of extcon as a **hotel switchboard operator**:

- Each **connector** (USB port, headphone jack, HDMI port) is a **hotel room**.
- The **provider driver** is the **front desk**: it monitors the physical door
  sensor and tells the switchboard when a guest (connector) arrives or leaves.
- The **extcon core** is the **switchboard**: it records occupancy and rings all
  interested parties.
- The **consumer drivers** (USB controller, audio driver, display manager) are
  **hotel services** (room service, housekeeping) that wait for the switchboard
  to call them when a guest checks in or out.
- **udev/systemd** are **external agencies** notified by courier (uevent) rather
  than direct phone call.

---

## References

- `include/linux/extcon.h` — Cable IDs and consumer API
- `include/linux/extcon-provider.h` — Provider registration API
- `Documentation/driver-api/extcon.rst` (upstream kernel docs)
- `drivers/extcon/extcon.c` — Core implementation
