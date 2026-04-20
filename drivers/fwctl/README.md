# Linux Kernel fwctl (Firmware Control) Subsystem

## Overview

**fwctl** (introduced in Linux 6.12, NVIDIA) is a generic framework that
exposes a device's **firmware management interface** to privileged userspace
via a character device (`/dev/fwctl/fwctl<N>`).

The key abstraction is a **firmware RPC** (Remote Procedure Call): userspace
sends an opaque binary payload (matching the device's firmware mailbox format)
and receives a response. The framework adds:

1. **Security scopes** — tiered access control so tools can use firmware
   interfaces without unrestricted hardware access
2. **Kernel-taint gating** — invasive debug RPCs taint the kernel
3. **A generic ABI** — common `FWCTL_INFO` / `FWCTL_RPC` ioctls across vendors

Currently upstream hardware providers: **mlx5** (Mellanox/NVIDIA SmartNICs)
and **pds** (Pensando/AMD Elba DPU).

Source: `drivers/fwctl/`

---

## Subsystem Stack

```
┌────────────────────────────────────────────────────────────────────┐
│                     Userspace tools                                 │
│                                                                     │
│  mlx5ctl   pdsctl   vendor-specific management tools               │
│  open("/dev/fwctl/fwctl0")                                         │
│  ioctl(FWCTL_INFO)   → query device type / capabilities            │
│  ioctl(FWCTL_RPC)    → send firmware command, receive response     │
└──────────────────────────┬─────────────────────────────────────────┘
                           │
┌──────────────────────────▼─────────────────────────────────────────┐
│                fwctl Core (main.c)                                  │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  fwctl_device                                                 │  │
│  │  ├── fwctl_ops  { open_uctx, close_uctx, info, fw_rpc }      │  │
│  │  ├── cdev → /dev/fwctl/fwctl<N>                               │  │
│  │  └── IDA minor number allocation                             │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  fwctl_uctx (per open() file descriptor)                      │  │
│  │  ├── fwctl_device *fwctl                                      │  │
│  │  └── driver private state (extended by provider)             │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                     │
│  Security scopes (fwctl_rpc_scope):                                 │
│  0: CONFIGURATION          — device config, no taint               │
│  1: DEBUG_READ_ONLY         — read-only debug, no taint             │
│  2: DEBUG_WRITE             — lockdown-compatible debug, taints     │
│  3: DEBUG_WRITE_FULL        — full debug write access, taints       │
└──────────────────────────┬─────────────────────────────────────────┘
                           │ fwctl_ops.fw_rpc()
       ┌───────────────────┴─────────────────┐
       ▼                                     ▼
┌──────────────────────┐         ┌────────────────────────────┐
│  mlx5 provider       │         │  pds provider              │
│  (fwctl/mlx5/)       │         │  (fwctl/pds/)              │
│                      │         │                            │
│  mlx5ctl_dev         │         │  Pensando/AMD Elba DPU     │
│  mlx5_core_dev *mdev │         │  firmware mailbox          │
│                      │         │                            │
│  Firmware mailbox:   │         │                            │
│  opcode + uid +      │         │                            │
│  op_mod → mailbox    │         │                            │
│  command interface   │         │                            │
└──────────────────────┘         └────────────────────────────┘
```

---

## Layer-by-Layer Explanation

### 1. fwctl_device — Provider Registration

A driver (e.g., mlx5) calls `fwctl_alloc_device()` + `fwctl_register()`:

```c
struct mlx5ctl_dev {
    struct fwctl_device fwctl;  /* must be first */
    struct mlx5_core_dev *mdev;
};

/* Ops table */
static const struct fwctl_ops mlx5ctl_ops = {
    .open_uctx    = mlx5ctl_open_uctx,
    .close_uctx   = mlx5ctl_close_uctx,
    .info         = mlx5ctl_info,        /* FWCTL_INFO */
    .fw_rpc       = mlx5ctl_fw_rpc,      /* FWCTL_RPC */
};
```

`fwctl_register()` allocates a minor number, creates `/dev/fwctl/fwctl<N>`,
and registers the `cdev` with the fwctl class.

### 2. fwctl_uctx — Per-FD Context

Each `open()` of `/dev/fwctl/fwctlN` creates a `fwctl_uctx`. Drivers can
embed their own state by extending this struct (e.g., `mlx5ctl_uctx` adds
`uctx_caps` and `uctx_uid`). The framework calls `ops->open_uctx()` on open
and `ops->close_uctx()` on release.

### 3. FWCTL_INFO ioctl

Returns device type information:

```c
struct fwctl_info {
    __u32 size;
    __u32 flags;
    __u32 out_device_type;   /* e.g., FWCTL_DEVICE_TYPE_MLX5 */
    __u32 device_data_len;
    __u64 out_device_data;   /* driver-specific capability data */
};
```

Userspace first calls `FWCTL_INFO` to learn `out_device_type`, which tells
it the format to use for `FWCTL_RPC` payloads.

### 4. FWCTL_RPC ioctl — Firmware RPC

The main operation: send a binary payload to firmware and receive a response.

```c
struct fwctl_rpc {
    __u32 size;
    __u32 scope;       /* fwctl_rpc_scope: 0=config, 1=ro-debug, 2=rw-debug, 3=full */
    __u32 in_len;      /* payload length (max 2 MiB) */
    __u32 out_len;     /* response buffer length */
    __u64 in;          /* userspace pointer to input payload */
    __u64 out;         /* userspace pointer to output buffer */
};
```

The framework:
1. Validates `scope` against kernel taint state and security policy
2. Copies `in` from userspace (max `MAX_RPC_LEN = 2 MiB`)
3. Calls `ops->fw_rpc(uctx, scope, in, in_len, &out_len)`
4. Copies response back to userspace

**Taint behavior:**
- `FWCTL_RPC_DEBUG_WRITE` and `FWCTL_RPC_DEBUG_WRITE_FULL` set `TAINT_FWCTL`
- Once tainted, `fwctl_tainted` is set and further invasive RPCs are tracked

### 5. Security Scope Model

```
FWCTL_RPC_CONFIGURATION (0)
  ├── Normal device configuration
  ├── Available to any process with CAP_NET_ADMIN
  └── No kernel taint

FWCTL_RPC_DEBUG_READ_ONLY (1)
  ├── Read diagnostic/telemetry data from firmware
  ├── Requires CAP_NET_ADMIN + kernel lockdown check
  └── No kernel taint

FWCTL_RPC_DEBUG_WRITE (2)
  ├── Write debug state (lockdown-compatible)
  ├── Taints kernel (TAINT_FWCTL)
  └── Blocked if kernel lockdown is active

FWCTL_RPC_DEBUG_WRITE_FULL (3)
  ├── Unrestricted firmware debug writes
  ├── Taints kernel (TAINT_FWCTL)
  └── Blocked if kernel lockdown is active
```

### 6. mlx5 Provider

Bridges fwctl to the mlx5 firmware mailbox:

- `mlx5ctl_info()`: returns `FWCTL_DEVICE_TYPE_MLX5` + UCTX capabilities
- `mlx5ctl_fw_rpc()`: wraps the userspace buffer in an mlx5 mailbox command
  (opcode + uid + op_mod header), sends via `mlx5_cmd_exec()`, copies
  response back

mlx5ctl uses the **auxiliary_bus** — it's an auxiliary device of the parent
`mlx5_core` PCI driver.

---

## RPC Flow Diagram

```
Userspace (mlx5ctl tool)
         │
         │ ioctl(fd, FWCTL_RPC, &rpc)
         │   scope = FWCTL_RPC_CONFIGURATION
         │   in = { mlx5_opcode, uid, op_mod, data... }
         ▼
fwctl core: fwctl_cmd_rpc()
         │
         ├─ scope check: taint if needed
         ├─ copy_from_user(in, in_len)
         ├─ ops->fw_rpc(uctx, scope, in_buf, in_len, &out_len)
         │                        │
         │                        ▼
         │              mlx5ctl_fw_rpc()
         │              mlx5_cmd_exec(mdev, in_buf, out_buf)
         │                        │
         │                        ▼
         │              Firmware mailbox → NIC firmware
         │                        │
         │              response ← NIC firmware
         │                        │
         │              returns out_buf
         │
         └─ copy_to_user(out, out_buf, out_len)
         │
         ▼
Userspace receives firmware response
```

---

## Files

| File | Purpose |
|------|---------|
| `main.c` | Core: cdev, ioctl dispatch, scope gating, open/close |
| `mlx5/main.c` | mlx5 (NVIDIA SmartNIC) firmware mailbox provider |
| `pds/main.c` | Pensando/AMD Elba DPU firmware provider |

**Key headers:**
- `include/linux/fwctl.h` — `fwctl_device`, `fwctl_ops`, `fwctl_uctx`
- `include/uapi/fwctl/fwctl.h` — `FWCTL_INFO`, `FWCTL_RPC`, `fwctl_rpc_scope`
- `include/uapi/fwctl/mlx5.h` — mlx5-specific device_data format

---

## HackMD Export

Title: **Linux Kernel fwctl (Firmware Control) Subsystem**

```bash
curl -X POST https://api.hackmd.io/v1/notes \
  -H "Authorization: Bearer $HACKMD_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"title\":\"Linux Kernel fwctl (Firmware Control) Subsystem\",\"content\":$(cat README.md | jq -Rs .)}"
```

---

## Test Cases

See [`fwctl_trace_test.py`](fwctl_trace_test.py) for bpftrace-based verification.
