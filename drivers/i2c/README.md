# Linux Kernel: i2c — I²C Bus Subsystem

> Source: `drivers/i2c/` — noble-linux-oem (oem-6.17-next)

---

## 1. What is the I²C subsystem?

**I²C** (Inter-Integrated Circuit) is a two-wire serial bus (SCL + SDA) for
connecting low-speed peripherals. The Linux I²C subsystem provides:

- A **bus abstraction** so device drivers don't care which I²C controller is
  underneath (Intel SMBus, Raspberry Pi BCM, Qualcomm GENI, etc.)
- A **client driver model** so sensors, EEPROMs, PMICs and displays register
  as `i2c_driver` objects matched by ACPI/DT ID tables
- **SMBus emulation** on pure I²C adapters
- **Slave mode** support for MCU-style use-cases
- **I²C multiplexers** (`i2c-mux.c`) to expand a single bus to many segments

---

## 2. Subsystem Stack

```
┌──────────────────────────────────────────────────────────────────────┐
│  USER SPACE                                                          │
│  /dev/i2c-N  (i2c-dev chardev)  → ioctl I2C_RDWR / I2C_SMBUS       │
└────────────────────────┬─────────────────────────────────────────────┘
                         │
┌────────────────────────▼─────────────────────────────────────────────┐
│  I2C CORE  (i2c-core-base.c + i2c-core-smbus.c)                     │
│  i2c_add_adapter() / i2c_del_adapter()                               │
│  i2c_transfer()  →  i2c_msg[]  →  adapter->algo->master_xfer()      │
│  i2c_smbus_xfer() → emulation or native SMBus                       │
│  Device-tree / ACPI instantiation (i2c-core-of.c, i2c-core-acpi.c)  │
│  i2c_register_driver() / i2c_new_client_device()                    │
└───────────┬───────────────────────────────┬──────────────────────────┘
            │  i2c_algorithm                │  i2c_mux
            ▼                               ▼
┌───────────────────────┐     ┌─────────────────────────────────────┐
│  BUS DRIVERS          │     │  I2C MUX LAYER  (i2c-mux.c)        │
│  (drivers/i2c/busses/)│     │  i2c_mux_add_adapter()              │
│  i2c-designware-*.c   │     │  Muxes: PCA9541, LTC4306, GPIO mux  │
│  i2c-i801.c (Intel)   │     │  i2c-atr.c  (Address Translator)   │
│  i2c-nforce2.c        │     └─────────────────────────────────────┘
│  i2c-qcom-geni.c      │
│  i2c-aspeed.c         │
│  i2c-bcm2835.c        │
│  i2c-mt65xx.c         │
│  i2c-amd-mp2-*.c      │
│  … 80+ adapters …     │
└───────────┬───────────┘
            │  master_xfer / smbus_xfer
            ▼
┌──────────────────────────────────────────────────────────────────────┐
│  HARDWARE  (I²C / SMBus controller)                                  │
│  SCL / SDA wires → sensors, EEPROMs, PMICs, displays …              │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 3. Key Data Structures

| Structure | Role |
|---|---|
| `struct i2c_adapter` | Represents one hardware I²C bus; holds `algo`, lock, bus number |
| `struct i2c_algorithm` | Bus driver vtable: `master_xfer`, `smbus_xfer`, `functionality` |
| `struct i2c_client` | One device on the bus (address + adapter pointer) |
| `struct i2c_driver` | Probe/remove + id_table; bound to an `i2c_client` |
| `struct i2c_msg` | A single read or write segment (addr, flags, len, buf) |
| `struct i2c_board_info` | Static (board-file) device registration |

---

## 4. Key API

### Adapter (bus driver)

```c
/* Register a new I²C controller */
int i2c_add_adapter(struct i2c_adapter *adap);
int i2c_add_numbered_adapter(struct i2c_adapter *adap);  /* fixed bus number */
void i2c_del_adapter(struct i2c_adapter *adap);
```

### Client driver

```c
/* Register a driver for I²C client devices */
int i2c_register_driver(struct module *owner, struct i2c_driver *drv);
void i2c_del_driver(struct i2c_driver *drv);

/* Transfer (called from client drivers) */
int i2c_transfer(struct i2c_adapter *adap, struct i2c_msg *msgs, int num);
int i2c_master_send(const struct i2c_client *client, const char *buf, int count);
int i2c_master_recv(const struct i2c_client *client, char *buf, int count);

/* SMBus helper */
s32 i2c_smbus_read_byte_data(const struct i2c_client *client, u8 command);
s32 i2c_smbus_write_byte_data(const struct i2c_client *client, u8 cmd, u8 val);
```

### Userspace (i2c-dev)

```c
/* Open /dev/i2c-N, set slave address */
ioctl(fd, I2C_SLAVE, addr);
ioctl(fd, I2C_RDWR, &rdwr);      /* arbitrary i2c_msg[] */
ioctl(fd, I2C_SMBUS, &smbus);    /* SMBus transaction */
ioctl(fd, I2C_FUNCS, &funcs);    /* query functionality bits */
```

---

## 5. Data-Flow: I²C Read Transaction

```
Client driver calls i2c_master_recv()
        │
        ▼
i2c_transfer(adap, msgs, 1)
        │
        ├─ acquire adap->bus_lock (mutex / rt_mutex)
        │
        ├─ adap->algo->master_xfer(adap, msgs, num)
        │      │
        │      └─ HW: set slave addr, toggle START, clock bytes,
        │              check ACK, generate STOP, signal completion
        │
        ├─ release adap->bus_lock
        │
        └─ return num_msgs_transferred (or -errno)
```

---

## 6. I²C Mux Layer

```
Adapter 0 (physical)
    └─ i2c_mux_add_adapter()  (for each mux channel)
           │
           ▼
       Virtual adapter (i2c-0-mux-1, i2c-0-mux-2, …)
           │  select channel, forward transfer, deselect
           ▼
       Physical adapter 0 → shared bus segment
```

`i2c-atr.c` (Address Translator) extends this for alias mapping: the physical
bus carries a different address than the virtual bus, allowing multiple
identical-address devices behind the mux.

---

## 7. Device Tree Binding

```dts
/* Controller */
i2c0: i2c@ff160000 {
    compatible = "qcom,geni-i2c";
    reg = <0xff160000 0x4000>;
    interrupts = <GIC_SPI 20 IRQ_TYPE_LEVEL_HIGH>;
    clocks = <&gcc GCC_QUPV3_WRAP1_S1_CLK>;
    #address-cells = <1>;
    #size-cells = <0>;

    /* Client */
    eeprom@50 {
        compatible = "atmel,24c32";
        reg = <0x50>;
    };
};
```

---

## 8. sysfs Layout

```
/sys/bus/i2c/
    devices/
        i2c-0/               ← adapter
            name
            0-0050/          ← client (bus 0, addr 0x50)
                modalias
                name
/sys/class/i2c-adapter/i2c-0/
/sys/class/i2c-dev/i2c-0/
/dev/i2c-0
/dev/i2c-1
…
```

---

## 9. Summary

The I²C subsystem's design separates:
- **Core** — locking, message routing, DT/ACPI instantiation
- **Algorithm** — hardware-specific byte transmission
- **Client** — device-specific protocol on top of byte streams

This layering lets over 80 different I²C controller drivers share one
userspace interface (`/dev/i2c-N`) and one driver model
(`i2c_driver` with probe/remove).
