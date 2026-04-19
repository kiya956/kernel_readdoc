# Linux Kernel: devfreq — Dynamic Voltage and Frequency Scaling for Non-CPU Devices

> Source: `drivers/devfreq/` — noble-linux-oem (oem-6.17-next)

---

## 1. What is devfreq?

**devfreq** is the kernel's DVFS framework for *non-CPU* devices: DDR
controllers, GPU memory buses, SoC fabric interconnects, and display
pipelines. It complements `cpufreq` (CPU DVFS) with the same power-saving
philosophy: measure load, pick the lowest OPP that satisfies demand.

---

## 2. Subsystem Stack

```
┌──────────────────────────────────────────────────────────────────┐
│                    USERSPACE                                     │
│  /sys/class/devfreq/<dev>/                                       │
│   cur_freq  available_frequencies  governor  min_freq max_freq   │
│   load      polling_interval       trans_stat                    │
└───────────────────────┬──────────────────────────────────────────┘
                        │ sysfs read/write
                        ▼
┌──────────────────────────────────────────────────────────────────┐
│                  DEVFREQ CORE  (devfreq.c)                       │
│                                                                  │
│  devfreq_add_device()     — register device + profile + governor │
│  devfreq_update_target()  — evaluate load, pick freq, apply      │
│  devfreq_monitor()        — periodic work (hrtimer/workqueue)    │
│  update_devfreq()         — governor poll entry point            │
│  devfreq_monitor_start/stop()                                    │
│                                                                  │
│  pm_qos integration: min/max freq constraints from QoS clients   │
│  thermal integration: devfreq_cooling → cap max freq on heat     │
└──────────┬────────────────────────────────────────────────────────┘
           │                         │
           ▼                         ▼
┌──────────────────────┐   ┌──────────────────────────────────────┐
│   GOVERNORS           │   │   DEVICE DRIVERS (providers)        │
│                       │   │                                     │
│  simple_ondemand.c    │   │  exynos-bus.c  (Samsung AXI bus)    │
│  ├─ busy_time/        │   │  imx-bus.c     (NXP i.MX bus)       │
│  │  total_time → %    │   │  imx8m-ddrc.c  (NXP DDR)            │
│  └─ upthreshold=90%   │   │  rk3399_dmc.c  (Rockchip DMC)       │
│                       │   │  tegra30-devfreq.c                  │
│  passive.c            │   │  sun8i-a33-mbus.c                   │
│  ├─ follows parent    │   │  hisi_uncore_freq.c                  │
│  │  (e.g., GPU→DDR)   │   │  mtk-cci-devfreq.c                  │
│                       │   │                                     │
│  performance.c        │   │  devfreq_dev_profile.target()       │
│  powersave.c          │   │  → clk_set_rate() or OPP set_rate() │
│  userspace.c          │   │    or regulator_set_voltage()       │
└──────────────────────┘   └──────────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────────────────────────────┐
│              OPP (Operating Performance Points)                  │
│  dev_pm_opp_find_freq_ceil/floor()  dev_pm_opp_set_rate()       │
│  Voltage-frequency table from DT or driver registration          │
└──────────────────────────────────────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────────────────────────────┐
│  Hardware: DDR scheduler / AXI bus arbiter / GPU memory clock    │
└──────────────────────────────────────────────────────────────────┘
```

---

## 3. Components

### 3.1 `devfreq.c` — Core

**Provider registration:**
```c
struct devfreq_dev_profile profile = {
    .target        = dmc_target,      // apply new freq
    .get_dev_status = dmc_get_status, // report busy/total time
    .get_cur_freq  = dmc_get_cur_freq,
    .polling_ms    = 50,
};
devm_devfreq_add_device(dev, &profile, DEVFREQ_GOV_SIMPLE_ONDEMAND, NULL);
```

**Monitoring loop:**
1. `devfreq_monitor()` fires via delayed work every `polling_ms`
2. Calls `update_devfreq()` → governor's `get_target_freq()`
3. Governor calls `devfreq_update_stats()` → `profile->get_dev_status()`
4. Governor returns target frequency
5. Core calls `profile->target(dev, &freq, flags)` to apply it
6. Core calls OPP layer: `dev_pm_opp_set_rate()` → `clk_set_rate()` + `regulator_set_voltage()`
7. `trace_devfreq_frequency()` emitted

### 3.2 Governors

| Governor | Strategy | Use case |
|---|---|---|
| `simple_ondemand` | busy% > threshold → go up; busy% < threshold-diff → step down | DDR, GPU, generic |
| `passive` | Mirror parent devfreq's freq ratio | DDR when GPU devfreq drives it |
| `performance` | Always max freq | Latency-critical paths |
| `powersave` | Always min freq | Ultra-low power |
| `userspace` | Userspace writes target freq | Testing, manual tuning |

**simple_ondemand threshold:**
```
busy_time / total_time > upthreshold (90%) → freq = max
busy_time / total_time < (upthreshold - downdifferential) → freq -= step
```

### 3.3 `devfreq-event.c` — Hardware Event Counters

Some SoCs (Exynos, Rockchip) have dedicated **devfreq event** hardware
(PPMU — Platform Performance Monitoring Unit) that counts DDR read/write
cycles directly. `devfreq_event_get_event()` gives precise bus utilization
without software instrumentation overhead.

### 3.4 `governor_passive.c` — Parent-Driven Scaling

Used when one device's frequency must track another (e.g., DDR bus frequency
tracks GPU frequency). Registers a notifier on the parent devfreq and scales
proportionally. No polling needed.

---

## 4. Data Flow: DDR Frequency Scaling

```
 Timer fires (polling_ms)
       │
 devfreq_monitor()
       │
 update_devfreq(devfreq)
       │
 governor->get_target_freq(devfreq, &freq)
       │
 ├─ devfreq_update_stats(devfreq)
 │    └─ profile->get_dev_status()
 │         └─ read PPMU counters (busy_cycles, total_cycles)
 │
 ├─ compute load% = busy / total
 │
 ├─ if load% > 90%: freq = max_freq
 │  elif load% < 85%: freq = freq - step
 │  else: freq unchanged
 │
 └─ devfreq_update_target(devfreq, freq)
       │
       ├─ dev_pm_opp_find_freq_ceil(dev, &freq)  — round up to valid OPP
       ├─ profile->target(dev, &freq, flags)
       │    └─ clk_set_rate(ddr_clk, freq)
       │         └─ DDR PLL reprogrammed
       │
       └─ trace_devfreq_frequency(devfreq, freq, prev_freq)
```

---

## 5. Key Data Structures

```c
struct devfreq {
    struct device dev;              // /sys/class/devfreq/<name>
    struct devfreq_dev_profile *profile;
    const struct devfreq_governor *governor;
    struct opp_table *opp_table;
    unsigned long previous_freq;
    unsigned long min_freq, max_freq;
    struct devfreq_dev_status last_status; // busy_time, total_time, current_frequency
    struct delayed_work work;       // polling work
    struct mutex lock;
    struct notifier_block nb;       // for passive governor
};

struct devfreq_dev_status {
    unsigned long total_time;
    unsigned long busy_time;
    unsigned long current_frequency;
    void *private_data;
};
```

---

## 6. Sysfs Interface

```
/sys/class/devfreq/<device-name>/
  governor          ← read/write: change governor at runtime
  cur_freq          ← current operating frequency (Hz)
  target_freq       ← last requested frequency
  min_freq          ← floor constraint (from pm_qos)
  max_freq          ← ceiling constraint (from pm_qos / thermal)
  available_frequencies  ← space-separated OPP list
  available_governors    ← installed governors
  polling_interval   ← monitor period (ms)
  load              ← last measured load%
  trans_stat        ← transition statistics table
```

---

## 7. Trace Events

| Tracepoint | Fires when |
|---|---|
| `devfreq:devfreq_frequency` | Frequency changed |
| `devfreq:devfreq_monitor` | Monitor work ran (every polling_ms) |

---

## 8. Summary

devfreq provides:
1. **Unified sysfs** for any non-CPU device DVFS — same interface regardless
   of whether it's a DDR controller, GPU bus, or SoC fabric.
2. **Pluggable governors** — swap between ondemand, passive, and userspace
   at runtime without driver changes.
3. **OPP + thermal + pm_qos integration** — frequency decisions respect
   thermal caps, QoS latency constraints, and hardware OPP tables.
4. **Event counter abstraction** — dedicated PPMU hardware gives precise
   utilization without software overhead.
