# Linux Kernel: pinctrl — Pin Controller Subsystem

> Source: `drivers/pinctrl/` — noble-linux-oem (oem-6.17-next)

---

## 1. What is the pinctrl subsystem?

The **pin controller subsystem** manages SoC I/O pads — physical pins that
can be assigned to different functions (GPIO, I2C, SPI, UART, …) and
configured for pull-up/down, drive strength, slew rate, schmitt trigger, etc.

It provides:
- **Pin multiplexing (pinmux)**: route a pin to a specific hardware function
- **Pin configuration (pinconf)**: set electrical characteristics
- **State machine**: each device can request named pin states (default, sleep,
  idle) that are applied on probe, suspend, resume
- Integration with **GPIO subsystem**: every GPIO is also a pin; the pinctrl
  core connects them via `gpio_request_enable` / `gpio_disable_free`

---

## 2. Subsystem Stack

```
┌──────────────────────────────────────────────────────────────────────┐
│  CONSUMERS  (device drivers, platform code)                          │
│  Automatic: kernel applies "default" pinctrl state during probe     │
│  Manual: devm_pinctrl_get(dev) → pinctrl_lookup_state() →          │
│          pinctrl_select_state(state)                                │
└──────────────────────────┬───────────────────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────────────────┐
│  PINCTRL CORE  (core.c, devicetree.c, pinmux.c, pinconf.c)          │
│  pinctrl_register() / pinctrl_dev_create()                           │
│  pinctrl_select_state()                                              │
│    → pinmux_enable_setting()   → pmxops->set_mux()                 │
│    → pinconf_apply_setting()   → confops->pin_config_set()          │
│  GPIO request: pinmux_gpio_request() → pmxops->gpio_request_enable()│
│  debugfs: /sys/kernel/debug/pinctrl/                                │
└──────────────────────────┬───────────────────────────────────────────┘
                           │ pinmux_ops / pinconf_ops / pinctrl_ops
                           ▼
┌──────────────────────────────────────────────────────────────────────┐
│  PIN CONTROLLER DRIVERS  (drivers/pinctrl/<vendor>/)                 │
│  pinctrl-intel.c / pinctrl-tigerlake.c — Intel PCH/TGL pads        │
│  pinctrl-amd.c    — AMD FCH GPIO/pinctrl                            │
│  pinctrl-rockchip.c — Rockchip IOMUX                               │
│  pinctrl-qcom.c / tlmm — Qualcomm TLMM                             │
│  pinctrl-imx.c    — NXP i.MX IOMUXC                                │
│  pinctrl-bcm2835.c — Broadcom BCM2835 GPIO+pinmux                  │
│  pinctrl-meson.c  — Amlogic Meson pinctrl                          │
│  … 50+ drivers …                                                    │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 3. Key Data Structures

| Structure | Role |
|---|---|
| `struct pinctrl_dev` | One pin controller instance; holds pin descriptors, registered groups/functions |
| `struct pinctrl` | Consumer handle; list of `pinctrl_state` objects |
| `struct pinctrl_state` | One named state ("default", "sleep"): list of settings |
| `struct pinctrl_setting` | One mux or conf operation on a pin group |
| `struct pinctrl_ops` | List pins, get groups, DT node mapping |
| `struct pinmux_ops` | `get_functions`, `set_mux`, `gpio_request_enable`, `gpio_disable_free` |
| `struct pinconf_ops` | `pin_config_get`, `pin_config_set`, `pin_config_group_set` |

---

## 4. Key API

### Consumer (device drivers)

```c
/* Usually automatic — kernel probes apply "default" state */

/* Manual state switching */
struct pinctrl *p = devm_pinctrl_get(dev);
struct pinctrl_state *s = pinctrl_lookup_state(p, PINCTRL_STATE_DEFAULT);
int ret = pinctrl_select_state(p, s);

/* Named states from DT */
/* pinctrl-0 = <&uart0_default>;  pinctrl-names = "default", "sleep"; */
```

### Provider (pin controller driver)

```c
struct pinctrl_dev *pinctrl_register(struct pinctrl_desc *pctldesc,
                                     struct device *dev, void *driver_data);
void pinctrl_unregister(struct pinctrl_dev *pctldev);

/* Map a GPIO offset to a pin number */
int pinctrl_gpio_request(struct gpio_chip *gc, unsigned int offset);
void pinctrl_gpio_free(struct gpio_chip *gc, unsigned int offset);
int pinctrl_gpio_direction_input(struct gpio_chip *gc, unsigned int offset);
int pinctrl_gpio_direction_output(struct gpio_chip *gc, unsigned int offset);
```

---

## 5. Data-Flow: Probe-time pin state application

```
Platform/DT probe detects "pinctrl-0" and "pinctrl-names" properties
        │
        ▼
really_probe(dev, drv)
    └─ pinctrl_bind_pins(dev)
           │
           ├─ devm_pinctrl_get(dev)   ← parse DT pinctrl-N properties
           │      └─ build list of pinctrl_state objects
           │
           └─ pinctrl_select_state(dev->pins->default_state)
                  │
                  ├─ for each mux setting:
                  │      pmxops->set_mux(pctldev, func_selector, group_selector)
                  │           └─ write IOMUX register
                  │
                  └─ for each conf setting:
                         confops->pin_config_set(pctldev, pin, configs, nconfs)
                              └─ write pull/drive/slew registers
```

---

## 6. Pin States Lifecycle

```
Device probe:   apply "default" state
      │
      ├── runtime:  driver may switch to "sleep" (low power)
      │              pinctrl_select_state(p, sleep_state)
      │
      └── resume:   switch back to "default"
                     pinctrl_select_state(p, default_state)
```

---

## 7. Device Tree Binding

```dts
/* Controller */
pinctrl: pinctrl@ff770000 {
    compatible = "rockchip,rk3399-pinctrl";
    reg = <0xff770000 0x1000>;

    uart0_default: uart0-default {
        rockchip,pins =
            <2 RK_PD0 1 &pcfg_pull_up>,  /* TX: func1, pull-up */
            <2 RK_PD1 1 &pcfg_pull_none>; /* RX: func1 */
    };

    uart0_sleep: uart0-sleep {
        rockchip,pins = <2 RK_PD0 0 &pcfg_pull_none>,
                        <2 RK_PD1 0 &pcfg_pull_none>;
    };
};

/* Consumer */
uart0: serial@ff180000 {
    pinctrl-names = "default", "sleep";
    pinctrl-0 = <&uart0_default>;
    pinctrl-1 = <&uart0_sleep>;
};
```

---

## 8. debugfs Layout

```
/sys/kernel/debug/pinctrl/
    pinctrl-maps         ← all mux/conf mappings
    <controller>/
        pins             ← pin name → number table
        groups           ← pin groups
        functions        ← available mux functions
        pinmux-pins      ← which function each pin is muxed to
        pinconf-pins     ← current electrical config per pin
        gpio-ranges      ← GPIO↔pin ranges
```

---

## 9. Summary

The pinctrl subsystem's **state machine** approach (default/sleep/idle) means
no driver needs to manually toggle IOMUX registers at runtime — the kernel
framework applies the right state automatically during `really_probe()`,
`suspend_device()`, and `resume_device()`, giving consistent pad behavior
across all SoCs with a single DT-driven configuration.
