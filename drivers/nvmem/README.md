# Linux Kernel: NVMEM (Non-Volatile Memory) Subsystem

> Source: `drivers/nvmem/` — noble-linux-oem (oem-6.17-next)

---

## 1. What is NVMEM?

The **NVMEM framework** provides a unified abstraction for any form of
small non-volatile storage: eFuses, OTP (One-Time Programmable) memories,
EEPROMs, battery-backed registers, and flash-stored environment variables.

The key insight is separating **providers** (hardware drivers) from
**consumers** (drivers that need to read calibration data, MAC addresses,
serial numbers, etc.) through named **cells** described in Device Tree.

---

## 2. Subsystem Stack

```
┌──────────────────────────────────────────────────────────────────┐
│                     USERSPACE                                    │
│  hexdump /sys/bus/nvmem/devices/*/nvmem                         │
│  nvmem-tool / udevadm  (read cells by name)                     │
└──────────────────────┬───────────────────────────────────────────┘
                       │  read/write binary sysfs file
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│           /sys/bus/nvmem/devices/<name>/nvmem  (sysfs bin attr) │
└──────────────────────┬───────────────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│                  NVMEM CORE  (core.c + layouts.c)                │
│                                                                  │
│  nvmem_register()          — provider registers device           │
│  nvmem_cell_get()          — consumer gets handle to named cell  │
│  nvmem_cell_read()         — read raw bytes from cell            │
│  nvmem_cell_read_u8/32/64  — typed convenience readers           │
│  nvmem_cell_write()        — write (if not read-only)            │
│  nvmem_device_read/write() — raw offset-based access             │
│  nvmem_add_one_cell()      — programmatic cell registration      │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  Layout bus  (layouts.c)                                 │   │
│  │  Interprets raw binary as structured cells               │   │
│  │  Drivers: onie-tlv, sl28vpd, u-boot-env                  │   │
│  └──────────────────────────────────────────────────────────┘   │
└──────┬───────────────────────────────────────────────────────────┘
       │  reg_read / reg_write callbacks
       ▼
┌───────────────────────────────────────────────────────────────────┐
│                    PROVIDER DRIVERS                               │
│                                                                   │
│  eFuse / OTP:                    EEPROM / SRAM:                   │
│  qfprom.c    (Qualcomm QFPROM)  rmem.c       (reserved memory)   │
│  rockchip-efuse.c               snvs_lpgpr.c (NXP Secure NVRAM)  │
│  sunxi_sid.c (Allwinner SID)    stm32-romem.c                    │
│  apple-efuses.c                 lpc18xx_eeprom.c                  │
│  imx-ocotp.c (NXP i.MX OTP)    rave-sp-eeprom.c                 │
│  mtk-efuse.c (MediaTek)         u-boot-env.c  (flash env block)  │
│  meson-efuse.c (Amlogic)                                          │
│  ... (30+ platform drivers)                                       │
└───────────────────────────────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────────┐
│  Hardware: eFuse array / OTP cells / EEPROM / battery-backed RAM │
└──────────────────────────────────────────────────────────────────┘
```

---

## 3. Components

### 3.1 `core.c` — Provider + Consumer API

**Provider side** — hardware drivers call:
```c
// nvmem_config describes the device
struct nvmem_config config = {
    .name    = "qfprom",
    .dev     = &pdev->dev,
    .size    = 0x1000,
    .reg_read  = qfprom_reg_read,
    .reg_write = NULL,          // read-only OTP
    .read_only = true,
};
nvmem = devm_nvmem_register(dev, &config);
```

**Consumer side** — other drivers (e.g., Ethernet, WiFi) call:
```c
cell = devm_nvmem_cell_get(dev, "mac-address");
buf  = nvmem_cell_read(cell, &len);  // returns kmalloc'd bytes
memcpy(mac_addr, buf, len);
kfree(buf);
```

**Cell description in Device Tree:**
```dts
efuse@0 {
    compatible = "qcom,qfprom";
    #address-cells = <1>;
    #size-cells = <1>;

    nvmem-cells:
    mac_address: mac-address@a8 {
        reg = <0xa8 0x6>;   /* offset=0xa8, size=6 bytes */
    };
    wifi_cal: calibration@1d00 {
        reg = <0x1d00 0x2f20>;
    };
};

/* Consumer: */
ethernet {
    nvmem-cells = <&mac_address>;
    nvmem-cell-names = "mac-address";
};
```

### 3.2 `internals.h` — nvmem_cell_entry

Internal cell representation:

```c
struct nvmem_cell_entry {
    const char *name;         // "mac-address"
    int         offset;       // byte offset within nvmem device
    size_t      raw_len;      // raw bytes to read before bit extraction
    int         bytes;        // final byte count
    int         bit_offset;   // first bit (for sub-byte cells)
    int         nbits;        // number of bits (0 = use bytes*8)
    nvmem_cell_post_process_t read_post_process; // optional transform
    struct nvmem_device *nvmem;
};
```

Bit-level cells allow packing multiple values within one byte — common in
eFuse arrays where each bit is individually blowable.

### 3.3 `layouts/` — Structured Interpretation

A **layout** driver binds to an nvmem device and programmatically adds cells
based on known binary formats:

| Layout | Description |
|---|---|
| `onie-tlv.c` | ONIE (Open Network Install Environment) TLV — network switch inventory |
| `sl28vpd.c` | Kontron SL28 Vital Product Data |
| `u-boot-env.c` | u-boot environment variables (CRC-checked key=value pairs in flash) |

Layout drivers register via `nvmem_layout_driver_register()` and call
`nvmem_add_one_cell()` for each field they discover.

### 3.4 `rmem.c` — Reserved Memory NVMEM

Wraps a `reserved-memory` region as an nvmem device. Used for:
- Bootloader → kernel communication (via well-known physical address)
- Board-specific calibration data stored in a fixed memory region

### 3.5 `u-boot-env.c` — U-Boot Environment Block

Reads a u-boot environment block from raw flash (MTD device), verifies
CRC32, and exposes each `key=value` pair as a named nvmem cell.

### 3.6 `stm32-bsec-optee-ta.c` — Secure eFuse via TEE

On STM32MP platforms, BSEC (Boot and Security Controller) eFuses are only
accessible from secure world. This driver proxies reads/writes via the TEE
subsystem (OP-TEE TA), combining nvmem + TEE in one pipeline.

---

## 4. Data Flow: Consumer Reading MAC Address

```
 Ethernet driver (consumer)         NVMEM core               eFuse HW
 ──────────────────────────         ──────────               ─────────
 1. devm_nvmem_cell_get(dev,
       "mac-address")
         │
 2. Core parses DT phandle ─────►  find nvmem_device
    lookup cell entry              offset=0xa8, bytes=6
         │
 3. nvmem_cell_read(cell)  ──────► __nvmem_reg_read()
                                   nvmem->reg_read(priv,
                                     0xa8, buf, 6)  ────────► eFuse read
                                                               6 raw bytes
         │
 4. Optional bit extraction
    (if nbits/bit_offset set)
         │
 5. Optional post_process()
    (e.g., MAC byte-swap)
         │
 6. buf returned to caller ◄────── kmalloc'd bytes
    memcpy to eth->addr
    kfree(buf)
```

---

## 5. Key APIs

**Provider:**
```c
struct nvmem_device *devm_nvmem_register(struct device *, const struct nvmem_config *);
int nvmem_add_one_cell(struct nvmem_device *, const struct nvmem_cell_info *);
```

**Consumer:**
```c
struct nvmem_cell *devm_nvmem_cell_get(struct device *, const char *id);
void    *nvmem_cell_read(struct nvmem_cell *, size_t *len);
int      nvmem_cell_write(struct nvmem_cell *, void *buf, size_t len);
int      nvmem_cell_read_u8(struct device *, const char *cell_id, u8 *);
int      nvmem_cell_read_u32(struct device *, const char *cell_id, u32 *);
int      nvmem_cell_read_u64(struct device *, const char *cell_id, u64 *);
int      nvmem_device_read(struct nvmem_device *, unsigned int offset,
                           size_t bytes, void *buf);
```

**Layout:**
```c
int nvmem_layout_driver_register(struct nvmem_layout_driver *);
```

---

## 6. Sysfs Layout

```
/sys/bus/nvmem/devices/
  qfprom0/
    nvmem           ← binary file: raw read/write (if rw)
    type            → "otp"
    read-only       → "1"
  imx-ocotp0/
    nvmem
    cells/          ← (some drivers expose per-cell attributes)
      mac-address
      serial-number
```

---

## 7. Hardware Types

| Category | Examples | Characteristics |
|---|---|---|
| **eFuse / OTP** | QFPROM, Rockchip, Sunxi SID, Apple | Write once; bit=1 is permanent |
| **EEPROM** | AT24, AT25 (via i2c-eeprom / spi-eeprom) | Byte-erasable; unlimited writes |
| **OTP SRAM** | NXP i.MX OCOTP | Shadow registers; program via fuse controller |
| **Battery-backed SRAM** | NXP SNVS, STM32 RTC backup regs | Volatile unless powered |
| **Flash env** | u-boot-env | Key=value block in NOR/NAND flash |
| **Secure eFuse** | STM32 BSEC via OP-TEE | Read via TEE |

---

## 8. Summary

NVMEM is a lightweight but complete framework that:

1. **Decouples hardware** (eFuse controller, EEPROM) from **consumers**
   (Ethernet MAC, WiFi calibration, bootloader args) through Device Tree
   phandles and named cells.
2. **Supports sub-byte granularity** — bit offsets and bit widths for
   densely packed eFuse arrays.
3. **Extends to structured formats** via layout drivers (ONIE TLV, u-boot env)
   that parse raw binary into named cells dynamically.
4. **Bridges secure hardware** (BSEC) via TEE integration, keeping the
   consumer API identical regardless of access path.
