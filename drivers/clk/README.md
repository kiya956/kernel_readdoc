# Linux Kernel: clk — Common Clock Framework (CCF)

> Source: `drivers/clk/` — noble-linux-oem (oem-6.17-next)

---

## 1. What is the CCF?

The **Common Clock Framework** (CCF) provides a unified abstraction for
hardware clock sources, PLLs, dividers, muxes, and gates. Before CCF each
SoC had its own ad-hoc clock code; CCF replaced this with a tree of
`clk_hw` objects where:

- Every clock knows its parent(s) and children
- `clk_set_rate()` propagates rate changes up and down the tree
- `clk_prepare/enable/disable/unprepare` manage power gating with
  reference counting
- The whole tree is visible in debugfs as a text tree

---

## 2. Subsystem Stack

```
┌──────────────────────────────────────────────────────────────────────┐
│  CONSUMERS  (CPU, GPU, I2C, SPI, UART, media clocks …)              │
│  clk_get(dev, "apb_clk")                                            │
│  clk_prepare_enable(clk)                                            │
│  clk_set_rate(clk, 100000000)                                       │
│  clk_get_rate(clk)                                                  │
└──────────────────────────┬───────────────────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────────────────┐
│  CCF CORE  (clk.c)                                                   │
│  clk_core: rate, accuracy, flags, enable_count, prepare_count       │
│  clk_prepare() / clk_enable() — two-stage gate open                 │
│  clk_set_rate() → clk_calc_new_rates() → clk_change_rate()         │
│  clk_set_parent() → reparent subtree                               │
│  Rate determination: clk_hw_ops->determine_rate / round_rate        │
│  debugfs: /sys/kernel/debug/clk/                                    │
└──────────────────────────┬───────────────────────────────────────────┘
                           │ clk_ops
                           ▼
┌──────────────────────────────────────────────────────────────────────┐
│  CLK HARDWARE TYPES  (clk-*.c)                                       │
│  clk-fixed-rate.c  — constant-rate oscillators (XO, crystal)        │
│  clk-divider.c     — integer / fractional divider                   │
│  clk-mux.c         — parent selection multiplexer                   │
│  clk-gate.c        — simple enable/disable gate                     │
│  clk-fixed-factor.c— mult/div of parent rate                        │
│  clk-pll*.c        — PLL types (integer, fractional, spread-spec)   │
│  clk-composite.c   — mux+divider+gate in one object                 │
└──────────────────────────┬───────────────────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────────────────┐
│  SoC CLOCK DRIVERS  (drivers/clk/<vendor>/)                          │
│  clk/qcom/  — Qualcomm GCC/CAMCC/DISPCC/VIDEOCC                    │
│  clk/rockchip/ — Rockchip CRU                                       │
│  clk/mediatek/ — MediaTek APMIXED/TOPCKGEN                         │
│  clk/samsung/ — Samsung CMU                                         │
│  clk/imx/   — NXP i.MX CCM                                          │
│  clk/bcm/   — Broadcom CPRMAN (RPi)                                 │
│  clk/intel/ — Intel CGU / LGM                                       │
│  … 50+ vendor subdirs …                                             │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 3. Key Data Structures

| Structure | Role |
|---|---|
| `struct clk_core` | Internal: rate, parent, children list, enable/prepare counts, spinlocks |
| `struct clk_hw` | Provider side: pointer to `clk_init_data` and `clk_ops`; embedded in hw clock struct |
| `struct clk` | Consumer handle: thin wrapper over `clk_core` |
| `struct clk_ops` | Driver vtable: `prepare`, `enable`, `set_rate`, `round_rate`, `set_parent`, `recalc_rate` |
| `struct clk_init_data` | Static metadata: name, ops, parent names, flags |

---

## 4. Key API

### Consumer (device drivers)

```c
/* Lookup */
struct clk *clk_get(struct device *dev, const char *id);
struct clk *devm_clk_get(struct device *dev, const char *id);
void clk_put(struct clk *clk);

/* Two-stage enable (prepare = may sleep; enable = atomic) */
int clk_prepare(struct clk *clk);
int clk_enable(struct clk *clk);
void clk_disable(struct clk *clk);
void clk_unprepare(struct clk *clk);
int clk_prepare_enable(struct clk *clk);     /* shorthand */
void clk_disable_unprepare(struct clk *clk); /* shorthand */

/* Rate */
int  clk_set_rate(struct clk *clk, unsigned long rate);
long clk_round_rate(struct clk *clk, unsigned long rate);
unsigned long clk_get_rate(struct clk *clk);

/* Parent */
int clk_set_parent(struct clk *clk, struct clk *parent);
struct clk *clk_get_parent(struct clk *clk);
```

### Provider (SoC clock drivers)

```c
/* Register a hw clock */
int clk_hw_register(struct device *dev, struct clk_hw *hw);
void clk_hw_unregister(struct clk_hw *hw);

/* Convenience constructors */
struct clk_hw *clk_hw_register_fixed_rate(struct device *dev, const char *name,
                                           const char *parent, unsigned long flags,
                                           unsigned long fixed_rate);
struct clk_hw *clk_hw_register_divider(struct device *dev, const char *name,
                                        const char *parent, unsigned long flags,
                                        void __iomem *reg, u8 shift, u8 width,
                                        u8 clk_divider_flags, spinlock_t *lock);
struct clk_hw *clk_hw_register_mux(struct device *dev, const char *name,
                                    const char **parent_names, u8 num_parents,
                                    unsigned long flags, void __iomem *reg, ...);
```

---

## 5. Data-Flow: clk_set_rate()

```
clk_set_rate(clk, target_rate)
        │
        ├─ clk_core_set_rate_nolock(core, rate)
        │
        ├─ clk_calc_new_rates(core, rate)
        │      │  traverse up: core->ops->determine_rate()
        │      │  find best parent rate and divider
        │      └─ mark new_rate on each clk_core in path
        │
        ├─ clk_change_rate(core)
        │      │
        │      ├─ if rate changed: core->ops->set_rate(hw, rate, parent_rate)
        │      │
        │      └─ propagate to children:
        │             for each child: clk_change_rate(child)
        │
        └─ notify CLOCK_CHANGED via blocking_notifier_chain_call
```

---

## 6. Two-Stage Enable Model

```
clk_prepare(clk)        ← may sleep (allocate resources, PLL lock wait)
    └─ ops->prepare()
clk_enable(clk)         ← atomic (just set register bits)
    └─ ops->enable()    ← spinlock held; no sleeping allowed

clk_disable(clk)        ← atomic
clk_unprepare(clk)      ← may sleep (release resources)
```

This split lets the clock framework safely handle both fast atomic paths
(IRQ handlers enabling a gate) and slow paths (PLL lock sequences).

---

## 7. Device Tree Binding

```dts
/* Provider: SoC clock controller */
cru: clock-controller@ff760000 {
    compatible = "rockchip,rk3399-cru";
    reg = <0xff760000 0x1000>;
    #clock-cells = <1>;
    assigned-clocks = <&cru ACLK_VOP0>, <&cru HCLK_VOP0>;
    assigned-clock-rates = <400000000>, <100000000>;
};

/* Consumer */
vop0: vop@ff900000 {
    clocks = <&cru ACLK_VOP0>, <&cru DCLK_VOP0>, <&cru HCLK_VOP0>;
    clock-names = "aclk_vop", "dclk_vop", "hclk_vop";
};
```

---

## 8. debugfs Layout

```
/sys/kernel/debug/clk/
    clk_summary           ← text table: name, enabled, rate, accuracy
    clk_dump              ← JSON-like tree dump
    <clk-name>/
        clk_rate          ← current rate in Hz
        clk_accuracy
        clk_phase
        clk_flags
        clk_enable_count
        clk_prepare_count
        clk_notifier_count
```

---

## 9. Summary

CCF's tree model means rate changes, reparenting, and enable/disable
propagate automatically. SoC drivers only implement `clk_ops` for their
specific hardware type (divider, mux, PLL) and register a flat list of
`clk_hw` objects; the framework assembles the tree from parent-name
strings, providing a single consistent view via debugfs.
