# Linux Kernel I3C (Improved Inter-Integrated Circuit) Subsystem

## Overview

**I3C** (MIPI Alliance's *Improved* I²C) is the next-generation embedded serial
bus designed to supersede I2C and SPI for sensor and peripheral connectivity.
Key improvements over I2C:

| Feature | I2C | I3C |
|---------|-----|-----|
| Max speed (SDR) | 400 kHz (fast) | 12.5 MHz |
| HDR-DDR | ✗ | 25 MHz |
| In-Band Interrupt | ✗ (needs separate IRQ pin) | ✓ (IBI over bus) |
| Dynamic address | ✗ (static) | ✓ (DAA — ENTDAA CCC) |
| Hot-join | ✗ | ✓ |
| Legacy I2C compat | — | ✓ (on same bus) |

The Linux I3C subsystem (introduced in 5.0, Boris Brezillon) provides:
- A **bus abstraction** (`i3c_bus`) managing mixed I3C+I2C devices
- A **master framework** (`master.c`) handling DAA, CCC, IBI
- A **device/driver model** (`device.c`, `i3c_driver`) for target device drivers

Source: `drivers/i3c/`

---

## Subsystem Stack

```
┌───────────────────────────────────────────────────────────────────┐
│                    I3C Device Drivers (consumers)                  │
│                                                                    │
│  struct i3c_driver { probe, remove, id_table }                     │
│  i3c_device_do_priv_xfer()  i3c_device_enable_ibi()               │
└────────────────────────────────┬──────────────────────────────────┘
                                 │ i3c_device
┌────────────────────────────────▼──────────────────────────────────┐
│                  I3C Core — device.c                               │
│                                                                    │
│  i3c_bus_type   i3c_driver_register()   i3c_driver_unregister()   │
│  match: BCR/DCR/PID from i3c_device_info                          │
└────────────────────────────────┬──────────────────────────────────┘
                                 │
┌────────────────────────────────▼──────────────────────────────────┐
│                  I3C Core — master.c                               │
│                                                                    │
│  ┌─────────────────────────────────────────────────────────────┐  │
│  │  i3c_bus                                                     │  │
│  │  ├── cur_master (i3c_dev_desc)                               │  │
│  │  ├── i3c_devs   (list of i3c_dev_desc)                       │  │
│  │  └── i2c_devs   (list of i2c_dev_desc — legacy devices)      │  │
│  └──────────────────────────┬──────────────────────────────────┘  │
│                             │                                      │
│  ┌──────────────────────────▼──────────────────────────────────┐  │
│  │  i3c_master_controller                                       │  │
│  │  ├── bus_init()   → RSTDAA + SETAASA + ENTDAA (DAA flow)     │  │
│  │  ├── do_daa()     → assign dynamic addresses                 │  │
│  │  ├── send_ccc_cmd() → broadcast/direct CCC commands          │  │
│  │  ├── priv_xfers() → SDR private transfers                    │  │
│  │  ├── i2c_xfers()  → I2C legacy transfers                     │  │
│  │  └── request_ibi() / enable_ibi() → IBI setup                │  │
│  └──────────────────────────────────────────────────────────────┘  │
└────────────────────────────────┬──────────────────────────────────┘
                                 │ i3c_master_controller_ops
┌────────────────────────────────▼──────────────────────────────────┐
│         Hardware Masters (drivers/i3c/master/)                     │
│                                                                    │
│  dw-i3c-master.c    DesignWare I3C IP (embedded in many SoCs)     │
│  svc-i3c-master.c   Silvaco/Microchip I3C master                  │
│  i3c-master-cdns.c  Cadence I3C master                            │
│  renesas-i3c.c      Renesas I3C master                            │
│  ast2600-i3c-master.c  ASPEED AST2600                             │
└────────────────────────────────┬──────────────────────────────────┘
                                 │
┌────────────────────────────────▼──────────────────────────────────┐
│              Physical I3C/I2C bus (2-wire: SCL + SDA)              │
│   I3C devices (dynamic addr)   Legacy I2C devices (static addr)   │
└───────────────────────────────────────────────────────────────────┘
```

---

## Layer-by-Layer Explanation

### 1. I3C Bus Protocol Concepts

**DAA — Dynamic Address Assignment:**
Before any I3C device can be used, the master runs the DAA procedure:
1. Send `ENTDAA` CCC (broadcast)
2. Devices without a dynamic address respond with their 48-bit Provisional ID + BCR + DCR
3. Master assigns a 7-bit dynamic address
4. Repeat until all devices are assigned
5. Master records each device's `PID/BCR/DCR` in `i3c_device_info`

**CCC — Common Command Codes:**
Bus management commands sent by the master:

| CCC | Direction | Purpose |
|-----|-----------|---------|
| `RSTDAA` | Broadcast | Reset all dynamic addresses |
| `ENTDAA` | Broadcast | Start DAA |
| `SETAASA` | Broadcast | Set static address as dynamic |
| `GETPID` | Direct | Read device Provisional ID |
| `GETBCR` | Direct | Read Bus Characteristics Register |
| `GETDCR` | Direct | Read Device Characteristics Register |
| `GETSTATUS` | Direct | Read device status |
| `SETMRL/SETMWL` | Direct | Set max read/write length |

**IBI — In-Band Interrupt:**
An I3C target can assert SDA low to signal the master without a separate IRQ
pin. The master recognizes the IBI, reads the initiating address + optional
payload, and calls the registered `ibi_handler()`. This replaces the need for
a dedicated interrupt line.

### 2. Core: master.c

The largest file (~3100 lines). Key responsibilities:

- **Bus initialization** (`i3c_master_register()`): calls `bus_init()` ops,
  runs DAA, enumerates legacy I2C devices from DT, creates sysfs devices.
- **CCC dispatch** (`i3c_master_send_ccc_cmd()`): routes broadcast and direct
  CCCs through the hardware ops.
- **Private transfer** (`i3c_master_do_priv_xfer()`): SDR read/write to a
  specific I3C device using its dynamic address.
- **IBI management**: `i3c_master_enable_ibi()` arms the hardware, routes
  incoming IBIs to the registered handler via a workqueue.
- **Hot-join**: handles new devices joining the bus after initialization.
- **Locking**: `i3c_bus` uses an `rwsem` — readers share, DAA/CCC/IBI
  management takes write lock.

### 3. Core: device.c

Thin layer exposing the `i3c_bus_type` to the driver model:
- `i3c_driver_register()` / `i3c_driver_unregister()`
- `i3c_device_match()`: matches drivers to devices by PID/BCR/DCR patterns
  in `i3c_device_id` tables
- `i3c_device_do_priv_xfer()`: consumer API — calls master's priv_xfers op

### 4. Key Data Structures

| Struct | Role |
|--------|------|
| `i3c_bus` | Physical bus: master ref, I3C+I2C device lists, rwsem |
| `i3c_master_controller` | Host controller: ops, bus, i2c_adap wrapper |
| `i3c_master_controller_ops` | bus_init, do_daa, send_ccc_cmd, priv_xfers, i2c_xfers, request_ibi, enable_ibi |
| `i3c_dev_desc` | Kernel-internal device: dynamic addr, IBI info, PID/BCR/DCR |
| `i3c_device` | User-facing device object (matched to `i3c_driver`) |
| `i3c_device_info` | PID, BCR, DCR, dynamic/static addr, max payload lengths |
| `i3c_driver` | Device driver: probe/remove, `i3c_device_id` table |
| `i3c_priv_xfer` | SDR transfer: rnw, len, data.in/out, err |
| `i3c_ccc_cmd` | CCC: id, dests[], payload, rnw, err |
| `i3c_ibi_setup` | IBI config: max_payload_len, num_slots, handler |

---

## Bus Initialization Flow

```
i3c_master_register(master, dev, ops, i2c_adap)
         │
         ▼
  bus_init(master)
         │
         ├─ RSTDAA broadcast  (reset all DA)
         ├─ SETAASA broadcast (static→dynamic for known devices)
         │
         ├─ ENTDAA loop:
         │   ┌─ device drives SDA low (ACK)
         │   ├─ master reads 48-bit PID + BCR + DCR
         │   ├─ master assigns dynamic address
         │   └─ repeat until no more ACKs
         │
         ├─ GETMRL/GETMWL per device
         ├─ i3c_master_add_i3c_dev_locked() for each discovered device
         │
         └─ Register i3c_device objects → match i3c_driver → probe()
```

---

## IBI Flow

```
I3C target (sensor)      Master controller         Kernel (master.c)
       │                        │                        │
       │ assert SDA low ────────►                        │
       │                        │ detect IBI             │
       │                        │ read initiator addr    │
       │◄─── ACK ───────────────┤                        │
       │ send payload (opt) ────►                        │
       │                        │ tegra_ibi_handler()    │
       │                        │ hte_push / workqueue ──►
       │                        │                   ibi_handler(dev, payload)
       │                        │                   (registered by i3c_driver)
```

---

## Private Transfer (SDR read/write)

```c
struct i3c_priv_xfer xfers[] = {
    { .rnw = false, .len = 1, .data.out = &reg_addr },
    { .rnw = true,  .len = 4, .data.in  = &buf },
};
ret = i3c_device_do_priv_xfer(dev, xfers, ARRAY_SIZE(xfers));
```

---

## Files

| File | Purpose |
|------|---------|
| `master.c` | Bus init, DAA, CCC, IBI, priv_xfers, hot-join |
| `device.c` | bus_type, driver_register, match, consumer xfer API |
| `internals.h` | Shared master↔device internals |
| `master/dw-i3c-master.c` | DesignWare IP (most common) |
| `master/svc-i3c-master.c` | Silvaco/Microchip |
| `master/i3c-master-cdns.c` | Cadence |
| `master/renesas-i3c.c` | Renesas |
| `master/ast2600-i3c-master.c` | ASPEED AST2600 BMC |

**Key headers:**
- `include/linux/i3c/master.h` — master_controller, ops, bus, dev_desc
- `include/linux/i3c/device.h` — i3c_driver, i3c_device, priv_xfer, IBI

---

## HackMD Export

Title: **Linux Kernel I3C (Improved Inter-Integrated Circuit) Subsystem**

```bash
curl -X POST https://api.hackmd.io/v1/notes \
  -H "Authorization: Bearer $HACKMD_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"title\":\"Linux Kernel I3C Subsystem\",\"content\":$(cat README.md | jq -Rs .)}"
```

---

## Test Cases

See [`i3c_trace_test.py`](i3c_trace_test.py) for bpftrace-based verification.
