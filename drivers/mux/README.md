# Linux Kernel: mux — Multiplexer Controller Subsystem

> Source: `drivers/mux/` — noble-linux-oem (oem-6.17-next)

---

## 1. What is the mux subsystem?

The **mux subsystem** abstracts hardware multiplexers — circuits that route
one of N inputs to a single output (or vice versa). Common uses:

- **I2C mux** — select which downstream I2C bus segment is active
- **ADC input mux** — select which analog input channel an ADC samples
- **SPI CS mux** — expand chip-select lines
- **GPIO mux** — select signal source for a GPIO line
- **Video input mux** — select camera source

---

## 2. Subsystem Stack

```
┌──────────────────────────────────────────────────────────────────┐
│  CONSUMERS (drivers, IIO, regmap, ...)                          │
│  mux_control_select(mux, state)                                 │
│  mux_control_deselect(mux)                                      │
│  mux_state_select(mstate)  / mux_state_deselect()              │
└───────────────────────┬──────────────────────────────────────────┘
                        │
                        ▼
┌──────────────────────────────────────────────────────────────────┐
│  MUX CORE  (core.c)                                             │
│  mux_chip_alloc() + mux_chip_register()                        │
│  mux_control_get() / devm_mux_control_get()  (consumer API)    │
│  Idle state management: IDLE_AS_IS / IDLE_DISCONNECT / state N  │
│  Mutex per mux_control (only one consumer at a time)           │
└────────────┬────────────────────────────────────────────────────┘
             │  mux_control_ops: set(mux, state)
             ▼
┌──────────────────────────────────────────────────────────────────┐
│  DRIVERS                                                        │
│  gpio.c   — GPIO-controlled mux (N GPIO bits → 2^N states)    │
│  mmio.c   — MMIO register-controlled mux                       │
│  adg792a.c — Analog Devices ADG792A analog switch              │
│  adgs1408.c — Analog Devices ADGS1408 mux                     │
└──────────────────────────────────────────────────────────────────┘
```

---

## 3. Key API

```c
/* Consumer: select mux state from Device Tree */
mux = devm_mux_control_get(dev, "adc-input");
mux_control_select(mux, 3);   /* select input 3 */
/* ... use resource ... */
mux_control_deselect(mux);    /* restore idle state */

/* Provider: register a GPIO mux with 3 control lines (8 states) */
chip = devm_mux_chip_alloc(dev, 1 /* n_mux */, sizeof_priv);
chip->ops = &gpio_mux_ops;
devm_mux_chip_register(dev, chip);
```

---

## 4. Device Tree Binding

```dts
mux: mux-controller {
    compatible = "gpio-mux";
    #mux-control-cells = <0>;
    mux-gpios = <&gpio0 3 GPIO_ACTIVE_HIGH>,
                <&gpio0 4 GPIO_ACTIVE_HIGH>;
    idle-state = <0>;
};

adc@0 {
    mux-controls = <&mux>;
    mux-control-names = "adc-input";
};
```

---

## 5. Summary

The mux subsystem provides a tiny but essential abstraction: any circuit
that selects one of N signal paths gets the same `mux_control_select/
deselect` API and DT binding, regardless of whether control is via GPIO,
MMIO, I2C switch, or analog switch IC.
