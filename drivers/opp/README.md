# Linux Kernel: OPP (Operating Performance Points)

> Source: `drivers/opp/` — noble-linux-oem (oem-6.17-next)

---

## 1. What is OPP?

An **Operating Performance Point** is a `(frequency, voltage)` pair at which
a device can legally operate. The OPP table describes all valid combinations
for a device — cpufreq, devfreq, and thermal all use it to pick the right
trade-off between performance and power.

---

## 2. Subsystem Stack

```
┌──────────────────────────────────────────────────────────────────┐
│               CONSUMERS                                          │
│  cpufreq    devfreq    thermal    GPU driver                     │
│  dev_pm_opp_find_freq_ceil()  dev_pm_opp_set_rate()             │
└──────────────────────┬───────────────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│                  OPP CORE  (core.c + of.c)                       │
│                                                                  │
│  opp_table  per-device table of OPP entries                      │
│  dev_pm_opp_find_freq_{exact,floor,ceil}()  — OPP lookup        │
│  dev_pm_opp_set_rate()  — lookup + apply freq+voltage atomically │
│  dev_pm_opp_add()       — programmatic OPP registration         │
│  of.c: parse operating-points-v2 DT nodes                       │
│  ti-opp-supply.c: TI-specific multi-supply OPP handling         │
└──────────────────────┬───────────────────────────────────────────┘
                       │ clk_set_rate() + regulator_set_voltage()
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│  Clock framework (clk)  +  Regulator framework                   │
└──────────────────────────────────────────────────────────────────┘
```

---

## 3. Device Tree Binding

```dts
cpu0: cpu@0 {
    compatible = "arm,cortex-a55";
    operating-points-v2 = <&cpu0_opp_table>;
    cpu-supply = <&buck2_reg>;
};

cpu0_opp_table: opp-table {
    compatible = "operating-points-v2";
    opp-shared;          /* all CPUs in cluster share this table */

    opp-300000000 {
        opp-hz = /bits/ 64 <300000000>;
        opp-microvolt = <825000>;
        opp-supported-hw = <0x3>;   /* bitmask: which SoC revisions */
    };
    opp-1200000000 {
        opp-hz = /bits/ 64 <1200000000>;
        opp-microvolt = <1100000>;
        clock-latency-ns = <300000>;
    };
    opp-1800000000 {
        opp-hz = /bits/ 64 <1800000000>;
        opp-microvolt = <1150000 1150000 1250000>; /* target, min, max */
        turbo-mode;      /* only if thermal budget allows */
    };
};
```

---

## 4. Key APIs

```c
/* Lookup */
struct dev_pm_opp *dev_pm_opp_find_freq_ceil(struct device *dev, unsigned long *freq);
struct dev_pm_opp *dev_pm_opp_find_freq_floor(struct device *dev, unsigned long *freq);

/* Apply (freq + voltage together) */
int dev_pm_opp_set_rate(struct device *dev, unsigned long target_freq);

/* Read OPP attributes */
unsigned long dev_pm_opp_get_freq(struct dev_pm_opp *opp);
unsigned long dev_pm_opp_get_voltage(struct dev_pm_opp *opp);
unsigned long dev_pm_opp_get_power(struct dev_pm_opp *opp);

/* Registration (when DT is not used) */
int dev_pm_opp_add_dynamic(struct device *dev, struct dev_pm_opp_data *data);

/* Scaling sequence (core internal) */
/* Up:   set voltage first, then clock */
/* Down: set clock first, then voltage */
```

---

## 5. Summary

OPP decouples the `(freq, voltage)` table from the drivers that use it.
A single DT node describes valid operating points; cpufreq, devfreq, and
thermal all query the same table via a clean API, ensuring consistent
voltage margins across all subsystems.
