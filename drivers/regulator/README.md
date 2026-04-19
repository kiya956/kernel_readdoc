# Linux Kernel: regulator — Voltage/Current Regulator Subsystem

> Source: `drivers/regulator/` — noble-linux-oem (oem-6.17-next)

---

## 1. What is the regulator subsystem?

The **regulator subsystem** abstracts hardware power regulators — LDOs, buck
converters, boost converters, and current sources — that supply voltage or
current to SoC subsystems and peripherals. It provides:

- **Consumer API**: `regulator_get`, `regulator_enable`, `regulator_set_voltage`
- **Driver API**: `regulator_register` for PMIC / power-rail drivers
- **Constraint enforcement**: min/max voltage, always-on, boot-on flags from DT
- **Reference-counted enable/disable** so multiple consumers can share a rail
- **Coupling and bypass**: supplies can be chained (supply rail → LDO → consumer)
- **Regulator notifier** chain for voltage-change events

---

## 2. Subsystem Stack

```
┌──────────────────────────────────────────────────────────────────────┐
│  CONSUMERS  (drivers: CPU, GPU, sensor, RF, display …)              │
│  regulator_get(dev, "vdd-core")                                      │
│  regulator_enable(reg)                                               │
│  regulator_set_voltage(reg, min_uV, max_uV)                         │
│  regulator_get_voltage(reg)                                          │
└──────────────────────────┬───────────────────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────────────────┐
│  REGULATOR CORE  (core.c)                                            │
│  regulator_enable_regmap() / regulator_disable_regmap()              │
│  _regulator_set_voltage() → constraint check → ops->set_voltage()   │
│  Reference counting: rdev->use_count / rdev->open_count             │
│  Supply chain: rdev->supply → parent regulator_dev                  │
│  Notifier chain: REGULATOR_EVENT_VOLTAGE_CHANGE etc.                │
│  debugfs: /sys/kernel/debug/regulator/                              │
└──────────────────────────┬───────────────────────────────────────────┘
                           │ regulator_ops
                           ▼
┌──────────────────────────────────────────────────────────────────────┐
│  REGULATOR DRIVERS  (drivers/regulator/)                             │
│  PMIC multi-rail: qcom-rpmh-regulator.c, da9xxx.c, max77686.c       │
│  Fixed: fixed.c (GPIO-switched, always-on)                          │
│  GPIO-controlled: gpio-regulator.c                                  │
│  I²C-controlled: fan53555.c (FAN53555 buck), tps65917.c             │
│  Regmap-based: most PMIC drivers use regmap for register I/O        │
│  … 200+ drivers …                                                   │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 3. Key Data Structures

| Structure | Role |
|---|---|
| `struct regulator_dev` | One regulator hardware instance: ops, constraints, supply, use_count |
| `struct regulator` | Consumer handle: points to `regulator_dev`, stores voltage range |
| `struct regulator_ops` | Driver vtable: `enable`, `disable`, `set_voltage`, `get_voltage`, `set_mode` |
| `struct regulator_desc` | Static description: name, id, type, ops, regmap fields |
| `struct regulator_init_data` | Constraints (min/max_uV, valid_modes, always_on) + supply_regulator |
| `struct regulator_config` | Runtime config passed to `regulator_register()` |

---

## 4. Key API

### Consumer

```c
/* Get/put */
struct regulator *regulator_get(struct device *dev, const char *id);
struct regulator *devm_regulator_get(struct device *dev, const char *id);
void regulator_put(struct regulator *regulator);

/* Enable/disable (reference counted) */
int  regulator_enable(struct regulator *regulator);
int  regulator_disable(struct regulator *regulator);
int  regulator_is_enabled(struct regulator *regulator);

/* Voltage */
int regulator_set_voltage(struct regulator *reg, int min_uV, int max_uV);
int regulator_get_voltage(struct regulator *regulator);

/* Current limit */
int regulator_set_current_limit(struct regulator *reg, int min_uA, int max_uA);
```

### Driver (provider)

```c
struct regulator_dev *regulator_register(struct device *dev,
                                         const struct regulator_desc *regulator_desc,
                                         const struct regulator_config *config);
void regulator_unregister(struct regulator_dev *rdev);

/* regmap helpers */
int regulator_enable_regmap(struct regulator_dev *rdev);
int regulator_disable_regmap(struct regulator_dev *rdev);
int regulator_set_voltage_sel_regmap(struct regulator_dev *rdev, unsigned sel);
```

---

## 5. Data-Flow: regulator_enable()

```
Consumer calls regulator_enable(reg)
        │
        ▼
regulator_enable() → _regulator_enable(rdev)
        │
        ├─ already enabled? → increment use_count, return 0
        │
        ├─ enable parent supply first (rdev->supply)
        │      └─ recursive _regulator_enable(supply_rdev)
        │
        ├─ rdev->desc->ops->enable(rdev)
        │      └─ regulator_enable_regmap() writes EN bit via regmap
        │
        ├─ rdev->use_count++
        │
        └─ send REGULATOR_EVENT_ENABLE on notifier chain
```

---

## 6. Voltage Scaling Flow

```
regulator_set_voltage(reg, min_uV, max_uV)
        │
        ├─ constraint check: within [min_uV .. max_uV] range?
        │
        ├─ ops->list_voltage() to find best selector
        │
        ├─ ops->set_voltage_sel(rdev, selector)
        │      └─ typically: regmap_update_bits(vsel_reg, vsel_mask, sel)
        │
        └─ if supply coupling needed:
               adjust parent voltage for headroom
```

---

## 7. Device Tree Binding

```dts
/* PMIC providing regulators */
pmic@5c {
    compatible = "dlg,da9210";
    reg = <0x5c>;

    DA9210_BUCKB: BUCKB {
        regulator-name = "vdd-cpu";
        regulator-min-microvolt = <700000>;
        regulator-max-microvolt = <1350000>;
        regulator-always-on;
        regulator-boot-on;
    };
};

/* Consumer */
cpu0 {
    cpu-supply = <&DA9210_BUCKB>;
};
```

---

## 8. sysfs / debugfs Layout

```
/sys/class/regulator/
    regulator.0/
        name
        status          ← enabled / disabled
        microvolts
        num_users
        type            ← voltage / current

/sys/kernel/debug/regulator/
    regulator_summary   ← tree view of all rails
    <regulator-name>/
        enable_count
        min_microvolts
        max_microvolts
        requested_microamps
        consumers
```

---

## 9. Summary

The regulator subsystem's key design decision is **reference-counted
enable/disable**: a rail stays on as long as any consumer holds it enabled,
and drops only when the last consumer calls `regulator_disable()`. Combined
with DT constraint enforcement and supply-chain traversal, this lets the
kernel safely manage complex power topologies (CPU rail feeding sub-LDOs
feeding IO rails) without drivers needing to know the topology themselves.
