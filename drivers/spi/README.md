# Linux Kernel: spi — SPI Bus Subsystem

> Source: `drivers/spi/` — noble-linux-oem (oem-6.17-next)

---

## 1. What is the SPI subsystem?

**SPI** (Serial Peripheral Interface) is a four-wire synchronous serial bus
(SCLK, MOSI, MISO, CS#). Compared to I²C it is faster, full-duplex, but
requires one chip-select line per device. Common uses: flash memories (NOR,
NAND), ADCs/DACs, RF transceivers, display controllers, TPMs.

The Linux SPI subsystem provides:
- A **controller abstraction** (`spi_controller`) so device drivers work on
  any SPI master (or slave) controller
- **Message/transfer queueing** with DMA mapping support
- **SPI offload** path for hardware-accelerated patterns (e.g., QSPI XIP)
- **CS GPIO** fallback when hardware CS is insufficient

---

## 2. Subsystem Stack

```
┌──────────────────────────────────────────────────────────────────────┐
│  USER SPACE                                                          │
│  /dev/spidevN.M  (spidev chardev)  → ioctl SPI_IOC_MESSAGE          │
└────────────────────────┬─────────────────────────────────────────────┘
                         │
┌────────────────────────▼─────────────────────────────────────────────┐
│  SPI CORE  (spi.c)                                                   │
│  spi_register_controller() / spi_alloc_host()                        │
│  spi_sync() → __spi_sync() → spi_execute_message()                  │
│  spi_async() → message queue → kthread / tasklet                    │
│  DMA: spi_map_buf() / spi_unmap_buf()                               │
│  Device instantiation: DT, ACPI, board-info                         │
│  spi_register_driver() / probe/remove matching                      │
└───────────┬────────────────────────────────┬─────────────────────────┘
            │ spi_controller_ops             │ CS / GPIO CS
            ▼                               ▼
┌───────────────────────┐    ┌──────────────────────────────────────┐
│  CONTROLLER DRIVERS   │    │  SPIDEV / SPI-NOR / SPI-NAND        │
│  spi-dw-*.c (DW/ARC)  │    │  spi-nor/  — read/write NOR flash   │
│  spi-pl022.c (ARM PL) │    │  spi-nand/ — read/write NAND flash  │
│  spi-geni-qcom.c      │    │  spi-mem.c — unified spi_mem_exec() │
│  spi-bcm2835.c        │    │  spidev.c  — raw userspace access   │
│  spi-omap2-mcspi.c    │    └──────────────────────────────────────┘
│  spi-intel-*.c        │
│  spi-amd.c            │
│  … 100+ drivers …     │
└───────────────────────┘
```

---

## 3. Key Data Structures

| Structure | Role |
|---|---|
| `struct spi_controller` | One SPI master or slave; holds `transfer_one`, `prepare_message`, CS GPIO array |
| `struct spi_device` | One peripheral on the bus (chip-select, mode, max_speed_hz) |
| `struct spi_driver` | probe/remove + id_table; bound to `spi_device` |
| `struct spi_message` | Linked list of `spi_transfer`; unit of work submitted to core |
| `struct spi_transfer` | One CS-asserted segment: tx_buf, rx_buf, len, speed, delay |
| `struct spi_mem_op` | Higher-level op for spi-mem (opcode + addr + dummy + data) |

---

## 4. Key API

### Controller (bus driver)

```c
/* Allocate and register a new SPI master */
struct spi_controller *spi_alloc_host(struct device *dev, unsigned extra);
int spi_register_controller(struct spi_controller *ctlr);
void spi_unregister_controller(struct spi_controller *ctlr);
```

### Client driver

```c
/* Register driver */
int spi_register_driver(struct spi_driver *sdrv);
void spi_unregister_driver(struct spi_driver *sdrv);

/* Transfer (sync / async) */
int spi_sync(struct spi_device *spi, struct spi_message *message);
int spi_async(struct spi_device *spi, struct spi_message *message);

/* Convenience wrappers */
int spi_write(struct spi_device *spi, const void *buf, size_t len);
int spi_read(struct spi_device *spi, void *buf, size_t len);
ssize_t spi_write_then_read(struct spi_device *spi,
                            const void *txbuf, unsigned n_tx,
                            void *rxbuf, unsigned n_rx);
```

### spi-mem (flash-friendly)

```c
int spi_mem_exec_op(struct spi_mem *mem, const struct spi_mem_op *op);
bool spi_mem_supports_op(struct spi_mem *mem, const struct spi_mem_op *op);
```

---

## 5. Data-Flow: spi_sync()

```
spi_sync(spi, msg)
    │
    ├─ __spi_validate(spi, msg)        ← check speed, bits, mode
    │
    ├─ __spi_sync()
    │      │
    │      ├─ if controller supports it: ctlr->transfer(spi, msg)
    │      │      immediately (polling/FIFO path)
    │      │
    │      └─ else: spi_async() path
    │             │  enqueue msg → ctlr->queue
    │             │  wake kthread (__spi_pump_messages)
    │             │
    │             └─ kthread calls:
    │                  ctlr->prepare_message()
    │                  ctlr->transfer_one_message()
    │                      └─ ctlr->transfer_one() per transfer
    │                             (may use DMA or PIO)
    │                  ctlr->unprepare_message()
    │
    └─ wait_for_completion(&msg->done)
         │
         └─ msg->complete(msg->context)  ← callback, or wake caller
```

---

## 6. SPI Modes (CPOL × CPHA)

| Mode | CPOL | CPHA | Clock idle | Sample on |
|---|---|---|---|---|
| SPI_MODE_0 | 0 | 0 | Low | Rising edge |
| SPI_MODE_1 | 0 | 1 | Low | Falling edge |
| SPI_MODE_2 | 1 | 0 | High | Falling edge |
| SPI_MODE_3 | 1 | 1 | High | Rising edge |

---

## 7. Device Tree Binding

```dts
spi0: spi@ff110000 {
    compatible = "snps,dw-apb-ssi";
    reg = <0xff110000 0x1000>;
    interrupts = <GIC_SPI 99 IRQ_TYPE_LEVEL_HIGH>;
    clocks = <&cru SCLK_SPI0>, <&cru PCLK_SPI0>;
    #address-cells = <1>;
    #size-cells = <0>;

    flash@0 {
        compatible = "jedec,spi-nor";
        reg = <0>;
        spi-max-frequency = <50000000>;
        spi-tx-bus-width = <4>;
        spi-rx-bus-width = <4>;
    };
};
```

---

## 8. sysfs Layout

```
/sys/bus/spi/
    devices/
        spi0.0/         ← spi_device (controller 0, CS 0)
            modalias
            driver -> ../../../../bus/spi/drivers/spidev
/sys/class/spidev/
    spidev0.0/
/dev/spidev0.0
```

---

## 9. Summary

The SPI subsystem's strength is its **message/transfer model**: any sequence
of CS-asserted segments is built as a `spi_message`, submitted with
`spi_sync/spi_async`, and the core handles DMA mapping, queuing, and
controller-specific sequencing. The **spi-mem** layer above adds a
command-address-data op abstraction for flash-like devices, enabling
controllers to accelerate QSPI read/write/erase in hardware.
