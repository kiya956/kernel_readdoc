# net/bpf — BPF Network Test Runner and Dummy Struct-ops

## Overview

`net/bpf/` is a small but critical directory in the Linux networking subsystem
that provides two things:

1. **`test_run.c`** — the kernel side of `BPF_PROG_TEST_RUN` (syscall
   `BPF_PROG_TEST_RUN` / `bpf_prog_test_run_opts`): allows userspace to invoke
   any loaded BPF program with a synthetic packet, socket buffer, or XDP frame
   and get the return code + timing back.  Used extensively by the BPF selftests
   and production systems that validate programs before attaching them.

2. **`bpf_dummy_struct_ops.c`** — a "dummy" implementation of `bpf_struct_ops`
   used for testing the struct_ops BPF verifier and map infrastructure without
   needing a real driver.

The actual BPF runtime (verifier, JIT, maps, helpers) lives in `kernel/bpf/`.
`net/bpf/` is specifically the **network-facing test runner** that bridges the
BPF subsystem to the networking data path for testing.

Source: `net/bpf/test_run.c`, `net/bpf/bpf_dummy_struct_ops.c`.

---

## Subsystem Stack

```
┌────────────────────────────────────────────────────────────────┐
│                        USERSPACE                               │
│  libbpf: bpf_prog_test_run_opts()                             │
│  syscall: BPF_PROG_TEST_RUN (bpf(2))                          │
└──────────────────────────────┬─────────────────────────────────┘
                               │ bpf(2) syscall
┌──────────────────────────────▼─────────────────────────────────┐
│            BPF SYSCALL DISPATCH  (kernel/bpf/syscall.c)        │
│  bpf_prog_test_run()                                           │
└──────────────────────────────┬─────────────────────────────────┘
                               │ calls prog_type test_run hook
┌──────────────────────────────▼─────────────────────────────────┐
│            TEST RUN CORE  (net/bpf/test_run.c)                 │
│                                                                 │
│  bpf_prog_test_run_skb()    — SK_SKB / SOCKET_FILTER / …      │
│  bpf_prog_test_run_xdp()    — XDP programs                    │
│  bpf_prog_test_run_flow_dissector() — flow dissector progs     │
│  bpf_prog_test_run_sk_lookup()      — SK_LOOKUP programs       │
│  bpf_prog_test_run_nf()             — Netfilter BPF programs   │
│  bpf_prog_test_run_raw_tp()         — raw tracepoint (net)     │
│                                                                 │
│  struct bpf_test_timer — measures per-run latency              │
│  Repeat mode: runs program N times, reports avg duration       │
│  XDP live-frame mode: feeds frame through the real XDP path    │
└──────────────────────────────┬─────────────────────────────────┘
                               │
┌──────────────────────────────┤  synthetic sk_buff / xdp_buff
                               │
┌──────────────────────────────▼─────────────────────────────────┐
│            BPF PROGRAM (user-supplied)                         │
│  Any loaded prog: XDP, TC, SK_SKB, cgroup-skb, …              │
│  Return code + modified packet returned to userspace           │
└──────────────────────────────┬─────────────────────────────────┘
                               │ (optionally forwarded to device)
┌──────────────────────────────▼─────────────────────────────────┐
│            LIVE-FRAME PATH (XDP only)                          │
│  bpf_test_run_xdp_live() → real net_device Tx queue           │
│  Allows measuring real NIC forwarding with BPF in the loop     │
└────────────────────────────────────────────────────────────────┘

  Dummy struct-ops path:
┌────────────────────────────────────────────────────────────────┐
│            DUMMY STRUCT_OPS  (bpf_dummy_struct_ops.c)          │
│                                                                 │
│  Registers bpf_dummy_ops as a struct_ops implementation        │
│  Allows testing BPF struct_ops maps, verifier rules, and       │
│  link semantics without a real kernel subsystem using them     │
│                                                                 │
│  bpf_dummy_ops:                                               │
│   .test_1 / .test_2 — dummy callback slots for BPF progs      │
└────────────────────────────────────────────────────────────────┘
```

---

## BPF_PROG_TEST_RUN in Practice

```c
// Load a BPF XDP program, then test it with a synthetic Ethernet frame:
struct bpf_test_run_opts opts = {
    .sz            = sizeof(opts),
    .data_in       = eth_frame,        // input packet bytes
    .data_size_in  = sizeof(eth_frame),
    .repeat        = 1000,             // run 1000 times
};
err = bpf_prog_test_run_opts(prog_fd, &opts);
printf("retval=%u duration=%u ns\n", opts.retval, opts.duration);
```

The kernel:
1. Allocates a synthetic `xdp_buff` (or `sk_buff`) from the data.
2. Calls the BPF program via `bpf_prog_run()` in a loop.
3. Returns: `retval` (XDP_PASS/XDP_DROP/…), `duration` (avg ns/run),
   optionally the modified packet bytes.

---

## Key Source Files

| File | Purpose |
|---|---|
| `net/bpf/test_run.c` | All `bpf_prog_test_run_*` implementations |
| `net/bpf/bpf_dummy_struct_ops.c` | Dummy struct_ops for testing |
| `kernel/bpf/syscall.c` | Dispatches `BPF_PROG_TEST_RUN` to test_run.c |
| `include/linux/bpf.h` | `bpf_prog_ops` — hook pointer for test_run |
| `tools/lib/bpf/bpf.c` | libbpf `bpf_prog_test_run_opts()` wrapper |

---

## Analogy

`BPF_PROG_TEST_RUN` is like a **flight simulator for network programs**:

- The flight simulator (test_run) provides a **synthetic cockpit** (fake
  packet buffer) that looks exactly like the real thing to the BPF program.
- The BPF program (the pilot) runs through all its logic and the simulator
  records what decision it made (return code) and how long it took (duration).
- The pilot never touches a real plane — but the training is 100% accurate
  because the cockpit is identical to production.
- **Live-frame mode** is like taking the trainee to an actual runway for a
  final check: the frame travels through the real NIC driver.

---

## References

- `net/bpf/test_run.c`
- `tools/testing/selftests/bpf/` — BPF selftests using test_run
- `Documentation/bpf/bpf_prog_run.rst`
- libbpf: `bpf_prog_test_run_opts()`
