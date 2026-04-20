# Linux Kernel Power Management Domain (pmdomain / genpd) Subsystem

## Overview

The **Generic Power Domain (genpd)** subsystem manages groups of hardware
blocks that share a common power rail or clock gate. When all devices in
a domain are idle, the domain can be powered off entirely — saving more
power than per-device runtime PM alone.

Typical use cases:
- GPU + display subsystem share a power island on an SoC
- USB controller + PHY must be powered together
- PCIe root complex + clock source form a domain
- CPU clusters (big.LITTLE) have per-cluster power domains

genpd integrates with the kernel's **runtime PM** framework: when a device
suspends via `rpm_suspend()`, genpd checks whether all devices in the domain
are idle and, if so, calls `power_off()`.

Source: `drivers/pmdomain/`

---

## Subsystem Stack

```
┌─────────────────────────────────────────────────────────────────────┐
│                Device drivers / consumers                            │
│                                                                      │
│  rpm_suspend(dev)   rpm_resume(dev)                                  │
│  dev_pm_genpd_set_performance_state(dev, state)                     │
│  dev_pm_genpd_add_notifier(dev, nb)                                  │
└────────────────────────────────┬────────────────────────────────────┘
                                 │ runtime PM callbacks
┌────────────────────────────────▼────────────────────────────────────┐
│              Runtime PM Core (drivers/base/power/runtime.c)          │
│  rpm_idle() → genpd_runtime_suspend() → genpd_power_off()           │
│  rpm_resume() → genpd_runtime_resume() → genpd_power_on()           │
└────────────────────────────────┬────────────────────────────────────┘
                                 │
┌────────────────────────────────▼────────────────────────────────────┐
│              genpd Core (drivers/pmdomain/core.c)                    │
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  generic_pm_domain (genpd)                                    │   │
│  │  ├── device_count   suspended_count   subdomain_count         │   │
│  │  ├── states[]  (power_off_latency_ns, power_on_latency_ns,    │   │
│  │  │              residency_ns, name)                           │   │
│  │  ├── power_off()  power_on()  (provider callbacks)            │   │
│  │  ├── set_performance_state()  (OPP integration)               │   │
│  │  ├── dev_list (gpd_device_info per device)                    │   │
│  │  └── parent_links / child_links (subdomain tree)              │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                      │
│  Governor (governor.c):                                              │
│  ├── default_suspend_ok()    check device latency constraints        │
│  └── _default_power_down_ok() check idle time ≥ off+on latency      │
│                                                                      │
│  gpd_list (global list of all registered domains)                    │
│  of_genpd_xlate_*() — DT lookup of domain by phandle                │
└────────────────────────────────┬────────────────────────────────────┘
                                 │ pm_genpd_init() / devm_pm_genpd_add_device()
┌────────────────────────────────▼────────────────────────────────────┐
│         SoC-specific genpd providers (drivers/pmdomain/<vendor>/)    │
│                                                                      │
│  qcom/   — Qualcomm QCOM RPMh/RPMpd power domains (MSM, SC, SM)     │
│  mediatek/— MediaTek SCPSYS power domains (MT8183, MT8192 …)         │
│  renesas/ — Renesas RPM/SYSC power domains (R-Car, RZ)              │
│  rockchip/— Rockchip PMU power domains (RK3399, RK3588 …)           │
│  samsung/ — Samsung Exynos power domains                            │
│  tegra/   — NVIDIA Tegra power domains                              │
│  imx/     — NXP i.MX power domains                                  │
│  arm/     — SCMI/SCPI power domains (firmware-managed)              │
│  apple/   — Apple PMGRv power state controllers                     │
│  ti/      — TI K3 / OMAP power domains                              │
│  + amlogic, bcm, st, starfive, sunxi, thead, xilinx, actions        │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Layer-by-Layer Explanation

### 1. generic_pm_domain — the central struct

```c
struct generic_pm_domain {
    struct dev_pm_domain domain;   /* plugs into runtime PM */
    struct list_head      dev_list;
    struct genpd_governor_data *gd;
    unsigned int          device_count;
    unsigned int          suspended_count;
    unsigned int          subdomain_count;
    int  (*power_off)(struct generic_pm_domain *);
    int  (*power_on)(struct generic_pm_domain *);
    int  (*set_performance_state)(struct generic_pm_domain *, unsigned int);
    struct genpd_power_state *states;  /* array of power states */
    unsigned int           state_count;
    unsigned int           state_idx;   /* current state */
    /* ... locking, DT, debugfs ... */
};
```

A domain has **N power states** (indexed 0…N-1), each with:
- `power_off_latency_ns` — time to enter the state
- `power_on_latency_ns` — time to exit the state
- `residency_ns` — minimum time the state must be held to break even on energy

### 2. Domain Tree (subdomain hierarchy)

Domains form a tree — a child domain cannot power off unless its parent
is also willing to power off. Conversely, powering on a child requires
first powering on its parent chain.

```
  CPU cluster domain (parent)
       │
       ├── CPU core domain (child)
       └── GPU domain (child)
                │
                └── Display domain (grandchild)
```

`pm_genpd_add_subdomain(parent, child)` links them. During `genpd_power_off()`,
the core walks up the tree recursively.

### 3. Governor (governor.c)

The governor decides **whether** to power off a domain at a given moment.
It evaluates two criteria:

**A. Device latency constraints:** each device has a PM QoS resume latency
constraint (`dev_pm_qos_resume_latency`). If powering the domain off would
cause a resume latency violation, the governor vetoes the power-off.

**B. Idle time vs. off+on latency:** only power off if the predicted idle
time exceeds `power_off_latency + residency`. Otherwise the domain would
waste more energy switching than staying on.

```
governor decision:
  idle_time_ns >= (power_off_latency_ns + residency_ns)  ?  power off  :  stay on
```

### 4. Runtime PM Integration

genpd hooks into runtime PM via `dev_pm_domain_attach()`. When a device's
`power.runtime_status` transitions to `RPM_SUSPENDED`:

```
rpm_suspend(dev)
    → pm_genpd_runtime_suspend(dev)
        → [stop device clocks if genpd has clock ops]
        → [check if all devices in domain are suspended]
        → if yes: genpd_power_off(genpd, ...)
            → governor check
            → _genpd_power_off(genpd) → provider's power_off()
            → recurse up parent chain
```

### 5. Performance States (OPP integration)

genpd can manage a domain's OPP (Operating Performance Point) — think of
it as a "performance domain" alongside power. `set_performance_state()` maps
to an OPP level (MHz/mV pair), allowing the domain to set the optimal
voltage/frequency for its current workload.

`dev_pm_genpd_set_performance_state(dev, state)` is the consumer API.

### 6. Device Tree binding

Providers register via `of_genpd_add_provider_simple()` or
`of_genpd_add_provider_onecell()`. Consumers request domains with:

```dts
/* Consumer node in DTS */
power-domains = <&pd_gpu>;
```

Kernel resolves this via `dev_pm_domain_attach()` at device probe time.

### 7. Debugfs

`/sys/kernel/debug/pm_genpd/` lists all registered domains:
- `pm_genpd_summary` — state, device count, latencies for all domains
- per-domain files with device list and transition counts

---

## Power-Off Flow Diagram

```
All devices in domain become RPM_SUSPENDED
              │
              ▼
  pm_genpd_runtime_suspend(last_dev)
              │
              ▼
  governor: default_power_down_ok()
       ├─ NO  → stay on (latency/residency not satisfied)
       └─ YES ↓
              ▼
  _genpd_power_off(genpd, timed=true)
       → ktime_get() start
       → genpd->power_off()    ← SoC provider disables power rail
       → measure latency → store in states[idx].power_off_latency_ns
              │
              ▼ (if has parent domains)
  genpd_power_off(parent, ...)  ← recurse up tree
```

---

## Key Data Structures

| Struct | Role |
|--------|------|
| `generic_pm_domain` | Domain: device list, states, power_on/off ops, governor data |
| `genpd_power_state` | One power state: off/on latency, residency, name |
| `gpd_device_info` | Per-device: td (timing), performance state, saved_state |
| `gpd_link` | Parent↔child subdomain link |
| `genpd_governor_data` | Governor: max_off_time_ns, next_wakeup, cached flags |
| `dev_pm_domain_attach_data` | DT attachment: flags (SYNC_DSTATE, etc.) |

---

## Provider Example: arm/scmi_pm_domain.c

For ARM platforms using SCMI (System Control and Management Interface):
- `power_off()` calls `scmi_power_state_set(domain_id, SCMI_POWER_OFF)`
- `power_on()` calls `scmi_power_state_set(domain_id, SCMI_POWER_ON)`
- The firmware (SCP/TF-A) handles the actual hardware

---

## Files

```
drivers/pmdomain/
├── core.c          genpd engine: registration, power_on/off, runtime PM hooks
├── governor.c      power-down decision logic (latency + residency checks)
├── arm/            SCMI/SCPI power domains (firmware-managed, ARM platforms)
├── qcom/           Qualcomm RPMh/RPMpd (most Snapdragon SoCs)
├── mediatek/       MediaTek SCPSYS
├── renesas/        Renesas R-Car / RZ SYSC
├── rockchip/       Rockchip RK PMU
├── samsung/        Exynos PMU
├── tegra/          NVIDIA Tegra PMC
├── imx/            NXP i.MX GPC
├── apple/          Apple PMGR power state
├── ti/             TI K3 / OMAP
└── ...             amlogic, bcm, st, starfive, sunxi, thead, xilinx, actions
```

---

## HackMD Export

Title: **Linux Kernel Power Management Domain (genpd) Subsystem**

```bash
curl -X POST https://api.hackmd.io/v1/notes \
  -H "Authorization: Bearer $HACKMD_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"title\":\"Linux Kernel Power Management Domain (genpd) Subsystem\",\"content\":$(cat README.md | jq -Rs .)}"
```

---

## Test Cases

See [`pmdomain_trace_test.py`](pmdomain_trace_test.py) for bpftrace verification.
