# Linux Kernel: hwspinlock — Hardware Spinlock Framework

> Source: `drivers/hwspinlock/` — noble-linux-oem (oem-6.17-next)

---

## 1. What is hwspinlock?

A **hardware spinlock** is a physical register in SoC fabric that provides
mutual exclusion between **different processors** (e.g., Linux ARM A-cores
and a DSP or MCU). Unlike a software spinlock, the hardware guarantees
atomicity across separate CPUs that share no cache-coherent memory.

---

## 2. Subsystem Stack

```
┌──────────────────────────────────────────────────────────────────┐
│  CONSUMERS (kernel drivers)                                     │
│  hwspin_lock_timeout(hwlock, timeout_ms)                        │
│  hwspin_trylock(hwlock)                                         │
│  hwspin_unlock(hwlock)                                          │
│  hwspin_lock_irqsave / hwspin_lock_in_atomic                   │
└───────────────────────┬──────────────────────────────────────────┘
                        │
                        ▼
┌──────────────────────────────────────────────────────────────────┐
│  HWSPINLOCK CORE  (hwspinlock_core.c)                           │
│  hwspin_lock_register() — platform driver registers bank        │
│  hwspin_lock_get_id()   — get numeric ID for a lock             │
│  of_hwspin_lock_get()   — DT-based lock acquisition             │
│  Radix tree maps lock ID → struct hwspinlock                    │
│  Retry loop (100 µs intervals) in atomic context                │
└──────────────────────┬───────────────────────────────────────────┘
                       │ hwspinlock_ops: trylock() / unlock() / relax()
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│  HARDWARE DRIVERS                                               │
│  omap_hwspinlock.c  — OMAP SpinLock module                     │
│  qcom_hwspinlock.c  — Qualcomm SFPB/TCSR hardware spinlock     │
│  sprd_hwspinlock.c  — UNISOC Spreadtrum                        │
│  stm32_hwspinlock.c — STM32 HSEM (Hardware Semaphore)          │
│  sun6i_hwspinlock.c — Allwinner H3/H6                          │
│  u8500_hsem.c       — Ericsson U8500 hardware semaphore        │
└──────────────────────────────────────────────────────────────────┘
```

---

## 3. Usage Pattern

```c
/* Acquire from Device Tree: */
hwlock = of_hwspin_lock_get(np, 0);

/* Use (with spin on busy): */
hwspin_lock_timeout(hwlock, 1000 /* ms */);
/* ... access shared resource between ARM + DSP ... */
hwspin_unlock(hwlock);

/* In atomic context: */
hwspin_lock_irqsave(hwlock, &flags);
/* ... */
hwspin_unlock_irqrestore(hwlock, flags);
```

---

## 4. Summary

hwspinlock provides a one-stop API for inter-processor mutual exclusion on
SoCs. The radix tree maps lock IDs to hardware register addresses; the
trylock/unlock callbacks are a single register read/write on the hardware,
making this the lowest-latency cross-processor synchronization primitive
in the kernel.
