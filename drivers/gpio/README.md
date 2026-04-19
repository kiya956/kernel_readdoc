# Linux Kernel: gpio — GPIO Subsystem

> Source: `drivers/gpio/` — noble-linux-oem (oem-6.17-next)

---

## 1. What is the GPIO subsystem?

**GPIO** (General-Purpose Input/Output) is the Linux abstraction for
individually-controllable digital pins. The subsystem provides:

- A **character device** (`/dev/gpiochipN`) with ioctl-based line access
  (GPIO uAPI v1 and v2)
- A **kernel API** (`gpiod_get`, `gpiod_set_value`, etc.) for in-kernel
  consumers
- A **descriptor-based model** that carries polarity, open-drain, pull
  configuration alongside the pin reference
- **IRQ mapping**: GPIO lines can be exposed as Linux IRQs via `gpio_irq_chip`
- **Aggregator** (`gpio-aggregator.c`): compose virtual gpiochips from
  existing ones

---

## 2. Subsystem Stack

```
┌──────────────────────────────────────────────────────────────────────┐
│  USER SPACE                                                          │
│  /dev/gpiochipN  → ioctl GPIO_GET_CHIPINFO_IOCTL                    │
│                    ioctl GPIO_V2_GET_LINE_IOCTL                      │
│                    read()  ← line events (edge detect)              │
└──────────────────────────┬───────────────────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────────────────┐
│  GPIO CHARDEV  (gpiolib-cdev.c)                                      │
│  GPIO_V2_GET_LINE_IOCTL → gpio_linehandle_create()                  │
│  GPIO_V2_LINE_SET_VALUES_IOCTL → gpiod_set_array_value()            │
│  poll/read for edge events via gpio_irq_chip                        │
└──────────────────────────┬───────────────────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────────────────┐
│  GPIOLIB CORE  (gpiolib.c, gpiolib-of.c, gpiolib-acpi.c)            │
│  gpiod_get(dev, "reset", GPIOD_OUT_HIGH)                            │
│  gpiod_set_value_cansleep(desc, 1)                                  │
│  gpiod_get_value(desc)                                              │
│  gpiochip_add_data() / gpiochip_remove()                            │
│  gpio_irq_chip: request_irq ↔ gpio_irq_startup/unmask              │
└──────────────────────────┬───────────────────────────────────────────┘
                           │ gpio_chip ops: get/set/direction/to_irq
                           ▼
┌──────────────────────────────────────────────────────────────────────┐
│  GPIO CONTROLLER DRIVERS  (drivers/gpio/)                            │
│  gpio-pl061.c  — ARM PL061                                          │
│  gpio-pca953x.c — NXP PCA953x I²C expander                         │
│  gpio-bd71828.c — ROHM PMIC GPIO                                    │
│  gpio-intel-*.c  — Intel PCH/Lynxpoint/Broxton                     │
│  gpio-amd-fch.c  — AMD FCH                                          │
│  gpio-mxc.c     — NXP i.MX                                         │
│  gpio-tegra186.c — NVIDIA Tegra                                     │
│  gpio-msm.c     — Qualcomm MSM                                      │
│  gpio-rockchip.c — Rockchip RK3xxx                                 │
│  … 200+ drivers …                                                   │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 3. Key Data Structures

| Structure | Role |
|---|---|
| `struct gpio_chip` | One GPIO controller: `get`, `set`, `direction_input/output`, `to_irq`, nr_gpios |
| `struct gpio_desc` | One GPIO line: chip pointer, offset, flags (open-drain, active-low, …) |
| `struct gpio_irq_chip` | IRQ mapping for GPIO controller: `irq_chip`, `handler`, `parent_handler` |
| `struct gpiod_lookup_table` | Static (board-file) mapping from device/function to gpio_chip+offset |

---

## 4. Key API

### Kernel consumers (preferred descriptor API)

```c
/* Get a GPIO descriptor from DT/ACPI/board-info */
struct gpio_desc *gpiod_get(struct device *dev, const char *con_id,
                            enum gpiod_flags flags);
struct gpio_desc *devm_gpiod_get(struct device *dev, const char *con_id,
                                 enum gpiod_flags flags);
void gpiod_put(struct gpio_desc *desc);

/* Set / Get value (aware of active-low polarity) */
void gpiod_set_value(struct gpio_desc *desc, int value);
void gpiod_set_value_cansleep(struct gpio_desc *desc, int value);
int  gpiod_get_value(const struct gpio_desc *desc);

/* Direction */
int gpiod_direction_output(struct gpio_desc *desc, int value);
int gpiod_direction_input(struct gpio_desc *desc);

/* IRQ */
int gpiod_to_irq(const struct gpio_desc *desc);
```

### Controller registration

```c
int gpiochip_add_data(struct gpio_chip *gc, void *data);
void gpiochip_remove(struct gpio_chip *gc);
int gpiochip_irqchip_add(struct gpio_chip *gc,
                         struct irq_chip *irqchip,
                         unsigned int first_irq,
                         irq_flow_handler_t handler,
                         unsigned int type);
```

---

## 5. Data-Flow: GPIO Line Event (edge interrupt)

```
Hardware pin: rising edge
      │
      ▼
GPIO controller HW IRQ fires
      │
      └─ parent IRQ handler (in gpio_irq_chip)
             │
             ├─ identifies which GPIO line fired
             │
             └─ generic_handle_irq(gpio_virq)
                    │
                    ├─ kernel consumer's IRQ handler (threaded or hardirq)
                    │
                    └─ OR: gpio chardev path
                           │
                           └─ kfifo_put(&le_req->buffer, event)
                                  │
                                  └─ wake_up_poll() → user read()
```

---

## 6. GPIO uAPI v2 (chardev)

```c
/* Open chip, query info */
int fd = open("/dev/gpiochip0", O_RDWR);
ioctl(fd, GPIO_GET_CHIPINFO_IOCTL, &chip_info);    /* name, label, lines */
ioctl(fd, GPIO_V2_GET_LINEINFO_IOCTL, &line_info); /* per-line metadata */

/* Request lines (output / input / edge-detect) */
struct gpio_v2_line_request req = {
    .offsets = {4, 5},
    .num_lines = 2,
    .config.flags = GPIO_V2_LINE_FLAG_OUTPUT,
};
ioctl(fd, GPIO_V2_GET_LINE_IOCTL, &req);
int line_fd = req.fd;

/* Set values */
struct gpio_v2_line_values vals = { .bits = 0b11, .mask = 0b11 };
ioctl(line_fd, GPIO_V2_LINE_SET_VALUES_IOCTL, &vals);

/* Edge events: read struct gpio_v2_line_event from line_fd */
```

---

## 7. Device Tree Binding

```dts
gpio0: gpio@ff720000 {
    compatible = "rockchip,gpio-bank";
    reg = <0xff720000 0x100>;
    interrupts = <GIC_SPI 51 IRQ_TYPE_LEVEL_HIGH>;
    clocks = <&cru PCLK_GPIO0>;
    gpio-controller;
    #gpio-cells = <2>;
    interrupt-controller;
    #interrupt-cells = <2>;
};

/* Consumer */
reset-gpios = <&gpio0 12 GPIO_ACTIVE_LOW>;
```

---

## 8. sysfs Layout

```
/sys/class/gpio/
    export            ← write line number to export (legacy API)
    unexport
    gpiochip0/        ← chip info
        base
        label
        ngpio
/dev/gpiochip0        ← chardev (preferred)
/dev/gpiochip1
…
```

---

## 9. Summary

The GPIO subsystem's evolution from the legacy integer-based API to the
**descriptor + chardev** model (uAPI v2) brings: polarity awareness,
open-drain support, per-line event queuing, and atomically consistent
multi-line operations — all without requiring kernel module code in
userspace applications.
