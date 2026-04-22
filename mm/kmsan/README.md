# KMSAN — Kernel Memory SANitizer

## Overview

**KMSAN (Kernel Memory SANitizer)** is a runtime detector for **use of
uninitialized memory** in the Linux kernel.  It tracks the *origin* of every
byte of kernel memory through every copy, arithmetic, and branch operation so
that when an uninitialized value reaches a security-sensitive sink (user copy,
network send, key comparison …) KMSAN prints a detailed report showing where
the bad memory was allocated and how it propagated.

KMSAN is an instrumented kernel build option (CONFIG_KMSAN).  It is only used
in dedicated fuzzing / testing kernels — never in production — because the 2×
memory overhead and ~3× slowdown are intentional trade-offs for coverage.

Source: `mm/kmsan/`, `include/linux/kmsan*.h`.

---

## Subsystem Stack

```
┌──────────────────────────────────────────────────────────────┐
│                       USER SPACE                             │
│  Receives sanitized data or a KMSAN report in dmesg          │
└───────────────────────────────┬──────────────────────────────┘
                                │  copy_to_user / sendmsg / …
┌───────────────────────────────▼──────────────────────────────┐
│                  REPORT SINK  (report.c)                      │
│                                                               │
│  kmsan_report()  ──  print origin chain + shadow dump        │
│                       into the kernel log                    │
└───────────────────────────────┬──────────────────────────────┘
                                │ triggered by check hooks
┌───────────────────────────────▼──────────────────────────────┐
│           SHADOW & ORIGIN TRACKING  (shadow.c)               │
│                                                               │
│  Every kernel memory address → two parallel regions:         │
│   • shadow   page  (1 bit per byte: 0=init, 1=uninit)        │
│   • origin   page  (4 bytes per 4 bytes: allocation site ID) │
│                                                               │
│  kmsan_get_metadata() maps any kernel VA to its shadow/origin│
│  kmsan_internal_poison_memory() marks memory uninit          │
│  kmsan_internal_unpoison_memory() marks memory init          │
└───────────────────────────────┬──────────────────────────────┘
                                │
┌───────────────────────────────▼──────────────────────────────┐
│          INSTRUMENTATION HOOKS  (hooks.c, instrumentation.c) │
│                                                               │
│  Compiler-inserted calls (Clang KMSAN ABI):                  │
│   __msan_warning()        — uninitialized check triggered    │
│   __msan_instrument_asm() — inline asm inputs/outputs        │
│   __msan_memset/memcpy    — propagate shadow through copies  │
│   __msan_poison_stack()   — stack frames poisoned on entry   │
│   __msan_chain_origin()   — build origin chain               │
│                                                               │
│  Kernel hooks (hooks.c):                                     │
│   kmsan_alloc_page()      — new page → poison shadow         │
│   kmsan_free_page()       — cleared on free                  │
│   kmsan_kmalloc()         — slab alloc → set shadow          │
│   kmsan_task_create/exit()— per-task state init              │
│   kmsan_copy_to_user()    — check before leaving kernel      │
└───────────────────────────────┬──────────────────────────────┘
                                │ read/write shadow pages
┌───────────────────────────────▼──────────────────────────────┐
│              CORE / INIT  (core.c, init.c)                   │
│                                                               │
│  kmsan_enabled — global flag (set after init completes)      │
│  __init kmsan_init_shadow() — maps shadow/origin for all     │
│                                direct-map pages at boot      │
│  per-task kmsan_context  — saves shadow/origin of current    │
│                            function arguments/return value   │
└───────────────────────────────┬──────────────────────────────┘
                                │
┌───────────────────────────────▼──────────────────────────────┐
│              LINUX MEMORY SUBSYSTEM                          │
│  Slab (SLUB), page allocator, vmalloc, stack allocator       │
│  Every allocation goes through KMSAN hooks to get shadow.    │
└──────────────────────────────────────────────────────────────┘
```

---

## Layer-by-Layer Explanation

### 1. Compile-Time Instrumentation

When `CONFIG_KMSAN=y`, Clang compiles every kernel C file with
`-fsanitize=kernel-memory`.  The compiler inserts calls like
`__msan_warning_with_origin()` before every branch/comparison of a tracked
value and `__msan_memcpy()` to propagate shadow through `memcpy`.

### 2. Shadow and Origin Memory (`shadow.c`)

For every byte of kernel memory there exists a **shadow byte** (0 = initialized,
1 = uninitialized).  For every 4-byte word there is a 4-byte **origin** storing
an encoded allocation site.

Shadow and origin are stored in separate memory regions mapped at boot:
`kmsan_init_shadow()` iterates `memblock` ranges and populates the shadow page
tables.

### 3. Hooks (`hooks.c`, `instrumentation.c`)

`hooks.c` intercepts every kernel allocator event:
- `kmsan_alloc_page()` — poisons all bytes of a new page (marks as uninit)
- `kmsan_slab_alloc()` — for slab: marks the object uninit if `__GFP_ZERO`
  is not set, marks it init if it is
- `kmsan_copy_to_user()` — the final safety net: calls `kmsan_report()` if any
  shadow bit is 1

### 4. Reports (`report.c`)

`kmsan_report()` dumps:
1. The allocation site (origin chain — where the uninitialized memory came from)
2. The "use" site (the copy_to_user, branch, or network send)
3. The shadow map for the affected bytes

### 5. Core State (`core.c`)

- `kmsan_enabled` global gate — hooks are no-ops until KMSAN is fully
  initialized to avoid false positives during early boot.
- Per-task `kmsan_context_state` — holds shadow/origin for function arguments
  and return values that pass through registers.

---

## Sample KMSAN Report

```
BUG: KMSAN: kernel-infoleak in write_sysrq_trigger+0x65/0x190
 write_sysrq_trigger+0x65/0x190
 proc_reg_write+0x1d5/0x290
 vfs_write+0x4fc/0xb40
 ksys_write+0x120/0x220
 __x64_sys_write+0x78/0xb0

Uninit was created at:
 slab_post_alloc_hook+0x5a/0x570
 kmalloc_trace+0x5b/0x210
 do_something_uninit+0x34/0x80

Bytes 0-7 of 16 are uninitialized
Memory access of size 16 at ffffc90003c4f930
```

---

## Key Data Structures

| Structure | Purpose |
|---|---|
| `kmsan_shadow_origin_ptr` | Pair of pointers to shadow + origin for one address |
| `kmsan_context_state` | Per-task argument / return-value shadow |
| `kmsan_origin` | 32-bit encoded allocation-site ID |
| Task's `kmsan_ctx` field | In `struct task_struct` via `#ifdef CONFIG_KMSAN` |

---

## Key Source Files

| File | Purpose |
|---|---|
| `mm/kmsan/core.c` | Initialization, `kmsan_enabled`, context state |
| `mm/kmsan/hooks.c` | Allocator / copy hooks |
| `mm/kmsan/instrumentation.c` | `__msan_*` ABI implementation |
| `mm/kmsan/shadow.c` | Shadow & origin page mapping |
| `mm/kmsan/report.c` | Uninitialized-use reporting |
| `mm/kmsan/init.c` | Boot-time shadow memory setup |
| `include/linux/kmsan.h` | Public API for allocators and copiers |
| `include/linux/kmsan_string.h` | String operation helpers |

---

## Analogy

KMSAN is like a **highlighter pen on every byte of kernel memory**:

- When memory is allocated uninitialized, KMSAN **highlights it in red**.
- When code initializes a byte, KMSAN **removes the highlight**.
- When code copies memory, KMSAN **copies the highlights too** — so even
  deeply nested copies carry the uninitialized taint.
- When a highlighted byte reaches the user or the network, KMSAN **shouts** —
  printing exactly where the red bytes came from and who tried to leak them.

---

## References

- `Documentation/dev-tools/kmsan.rst`
- `mm/kmsan/` — full implementation
- `include/linux/kmsan.h`
- Clang KMSAN documentation: https://clang.llvm.org/docs/MemorySanitizer.html
