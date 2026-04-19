# Linux Kernel: Counter Subsystem

> Source: `drivers/counter/` — noble-linux-oem (oem-6.17-next)

---

## 1. What is the Counter Subsystem?

The **counter subsystem** provides a unified interface for hardware pulse
counters, encoders, and timers. Use cases include:

- **Quadrature encoders** — motor shaft position/speed (robotics, CNC)
- **Pulse counters** — flow meters, tachometers, event counting
- **Capture timers** — measure pulse width, period, duty cycle
- **Timer channels** — STM32 general-purpose timers, Intel QEP

Before this subsystem, each driver exposed ad-hoc sysfs or character devices.
Now all expose a common `/dev/counter<N>` interface with read/write via
`ioctl(COUNTER_ADD_WATCH_IOCTL)` and blocking `read()` for events.

---

## 2. Subsystem Stack

```
┌──────────────────────────────────────────────────────────────────┐
│                    USERSPACE                                     │
│  libcounter  /  custom apps                                      │
│  read(/dev/counter0)  ← blocking: returns counter_event structs  │
│  ioctl(COUNTER_ADD_WATCH_IOCTL)  ← subscribe to component events │
│  /sys/bus/counter/devices/counter0/                              │
│    count0/count  signal0/level  count0/function  ...             │
└───────────────────────┬──────────────────────────────────────────┘
                        │
                        ▼
┌──────────────────────────────────────────────────────────────────┐
│           COUNTER CORE  (counter-core.c)                         │
│                                                                  │
│  counter_alloc()        — allocate counter_device + private data │
│  counter_add()          — register device (bus + chrdev + sysfs) │
│  counter_unregister()   — unregister                             │
│  counter_push_event()   — driver pushes event → read() wake      │
└────────────┬───────────────────────────┬─────────────────────────┘
             │                           │
             ▼                           ▼
┌─────────────────────────┐  ┌──────────────────────────────────┐
│  counter-sysfs.c        │  │  counter-chrdev.c                │
│  Per-component sysfs    │  │  /dev/counter<N>                 │
│  (count, direction,     │  │  COUNTER_ADD_WATCH_IOCTL         │
│   function, signal      │  │  blocking read() → event ring    │
│   level, extension)     │  │  poll() support                  │
└─────────────────────────┘  └──────────────────────────────────┘
             │
             ▼
┌──────────────────────────────────────────────────────────────────┐
│              HARDWARE DRIVERS                                    │
│                                                                  │
│  104-quad-8.c      — ACCES 104-QUAD-8 (ISA quadrature)         │
│  ftm-quaddec.c     — NXP FlexTimer Module quadrature decoder    │
│  i8254.c           — Intel 8254 PIT (legacy PC timer)           │
│  intel-qep.c       — Intel Quadrature Encoder Peripheral        │
│  interrupt-cnt.c   — Generic interrupt-based pulse counter       │
│  microchip-tcb-capture.c — Microchip AT91 Timer Counter capture │
│  rz-mtu3-cnt.c     — Renesas RZ/G2L MTU3 counter               │
│  stm32-lptimer-cnt.c — STM32 Low-Power Timer counter            │
│  stm32-timer-cnt.c   — STM32 General Timer counter              │
│  ti-ecap-capture.c  — TI eCAP (enhanced capture) unit           │
│  ti-eqep.c          — TI eQEP (enhanced quadrature encoder)     │
└──────────────────────────────────────────────────────────────────┘
             │
             ▼
┌──────────────────────────────────────────────────────────────────┐
│  Hardware: quadrature encoder inputs / pulse inputs / timer regs │
└──────────────────────────────────────────────────────────────────┘
```

---

## 3. Core Concepts

### 3.1 Signals

A **signal** represents a physical input line. Each signal has:
- `level` — current logical state (HIGH/LOW)
- `polarity` — normal or inverted

### 3.2 Counts

A **count** is a value that changes based on signal events. It has:
- `count` — the current value (u64)
- `direction` — FORWARD or BACKWARD (for decoders)
- `function` — how the count changes (PULSE, QUADRATURE_X1_A, etc.)
- `synapse` — which signal + action (rising/falling/both) triggers a count step
- `ceiling`, `floor` — rollover bounds

### 3.3 Components and Extensions

Everything is a **component** (`counter_comp`):
- `COUNTER_COMP_U64` — 64-bit count value
- `COUNTER_COMP_BOOL` — boolean (enable/disable)
- `COUNTER_COMP_ENUM` — named state (function, direction, mode)
- `COUNTER_COMP_ARRAY` — array of components (e.g., capture array)

Extensions add driver-specific attributes to counts, signals, or the device.

### 3.4 Watch / Events (chrdev)

Userspace subscribes to component changes:
```c
struct counter_watch watch = {
    .component.type    = COUNTER_COMPONENT_COUNT,
    .component.scope   = COUNTER_SCOPE_COUNT,
    .component.parent  = 0,    // count index
    .event             = COUNTER_EVENT_OVERFLOW,
};
ioctl(fd, COUNTER_ADD_WATCH_IOCTL, &watch);
// then read() blocks until event fires
struct counter_event evt;
read(fd, &evt, sizeof(evt));
// evt.value = count at event time
// evt.timestamp = ktime (ns)
```

Driver side:
```c
counter_push_event(counter, COUNTER_EVENT_OVERFLOW, channel);
```

---

## 4. Data Flow: Quadrature Encoder Position Tracking

```
 Motor shaft rotates
       │
 A/B quadrature signals toggle
       │
 Hardware timer (e.g., STM32 TIM)
 decodes direction, increments count
       │
 Timer interrupt / DMA
       │
 Driver: count_read callback
         reads TIM->CNT register
         returns current position
       │
 Sysfs poll / chrdev watch
       │
 Userspace: read /sys/bus/counter/devices/counter0/count0/count
            → motor position in encoder ticks

 Overflow/underflow → counter_push_event(OVERFLOW)
                   → read() on /dev/counter0 returns event
```

---

## 5. Sysfs Layout

```
/sys/bus/counter/devices/counter0/
  name                         ← driver name
  count0/
    count                      ← current count value (r/w)
    direction                  ← "forward" or "backward"
    function                   ← "quadrature x4" etc.
    ceiling                    ← overflow value
    floor                      ← underflow value
    enable                     ← start/stop counting
    synapse0/
      action                   ← rising/falling/both
      signal_id                ← which signal
  signal0/
    signal_id
    level                      ← "high" or "low"
    polarity                   ← "normal" or "inverted"
/dev/counter0                  ← chrdev: watch + read events
```

---

## 6. Summary

The counter subsystem:
1. **Unifies** disparate pulse counter / encoder hardware behind a single
   sysfs + chrdev interface — same userspace code works on STM32, TI, Intel.
2. **Supports event-driven access** — `read()` on `/dev/counter<N>` blocks
   until a watched event (overflow, capture, change) fires, avoiding polling.
3. **Composable model** — signals, counts, synapses, and extensions are
   independent components, making it easy to represent simple pulse counters
   and complex multi-channel capture timers alike.
