# Linux Kernel: TEE (Trusted Execution Environment) Subsystem

> Source: `drivers/tee/` — noble-linux-oem (oem-6.17-next)

---

## 1. What is TEE?

A **Trusted Execution Environment** is an isolated, hardware-enforced execution
context where code runs with higher privilege and confidentiality than the
normal OS. The classic implementation is **Arm TrustZone**, which splits
the processor into two "worlds":

| World | Name | Runs |
|---|---|---|
| **Normal World** (NW) | REE (Rich Execution Environment) | Linux + userspace |
| **Secure World** (SW) | TEE (Trusted Execution Environment) | Trusted OS (OP-TEE OS, AMD PSP) |

World transitions happen via **SMC** (Secure Monitor Call, AArch64) or
**FF-A** (Firmware Framework for Arm, newer standard).

**Trusted Applications (TAs)** run inside the TEE — e.g., key storage,
DRM content decryption, fingerprint matching, secure boot verification.

---

## 2. Subsystem Stack

```
┌──────────────────────────────────────────────────────────────────┐
│                       USERSPACE                                  │
│  libteec (GlobalPlatform TEE Client API)                         │
│  tee-supplicant daemon  (TA loading, RPMB, fs access from TEE)  │
│  Applications (DRM clients, keystore, fingerprint, FIDO2)        │
└───────────────────┬──────────────────────────────────────────────┘
                    │  open/ioctl  (GlobalPlatform ioctls)
                    ▼
┌──────────────────────────────────────────────────────────────────┐
│                UAPI  /dev/tee0  /dev/teepriv0                    │
│  TEE_IOC_VERSION          — get TEE implementation info          │
│  TEE_IOC_OPEN_SESSION     — create session with a TA             │
│  TEE_IOC_INVOKE           — invoke command in session            │
│  TEE_IOC_CANCEL           — cancel pending operation             │
│  TEE_IOC_CLOSE_SESSION    — close session                        │
│  TEE_IOC_SHM_ALLOC        — allocate shared memory               │
│  TEE_IOC_SHM_REGISTER     — register existing memory as shared   │
│  TEE_IOC_SUPPL_RECV/SEND  — supplicant RPC channel               │
└──────────────┬──────────────────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────────────────────────┐
│              TEE CORE  (tee_core.c / tee_shm.c)                  │
│                                                                  │
│  tee_device_alloc/register()  — backend driver registration      │
│  teedev_open()               — context creation per process      │
│  tee_shm_alloc_user_buf()    — shared memory allocation          │
│  tee_shm_register_user_buf() — register user pages               │
│  ioctl dispatch → driver ops (open_session, invoke_func, ...)    │
└──────┬──────────────────────┬────────────────────────────────────┘
       │                      │
       ▼                      ▼
┌──────────────┐    ┌──────────────────────────────────────────────┐
│ amdtee/      │    │  optee/                                      │
│ AMD TEE      │    │  OP-TEE (Open Portable TEE)                  │
│ (PSP firmware│    │                                              │
│  via mailbox)│    │  ┌──────────────┐  ┌──────────────────────┐  │
└──────────────┘    │  │ smc_abi.c    │  │ ffa_abi.c            │  │
                    │  │ TrustZone    │  │ FF-A (Firmware       │  │
┌──────────────┐    │  │ SMC calling  │  │ Framework for Arm)   │  │
│ tstee/       │    │  │ convention   │  │ (SPMC-based)         │  │
│ Trusted Svcs │    │  └──────────────┘  └──────────────────────┘  │
│ TEE (FF-A)   │    │  ┌──────────────┐  ┌──────────────────────┐  │
└──────────────┘    │  │ call.c       │  │ rpc.c                │  │
                    │  │ session/cmd  │  │ secure→normal RPC    │  │
                    │  │ serialization│  │ (TA loading, RPMB)   │  │
                    │  └──────────────┘  └──────────────────────┘  │
                    │  ┌──────────────┐  ┌──────────────────────┐  │
                    │  │ supp.c       │  │ notif.c              │  │
                    │  │ supplicant   │  │ async notifications  │  │
                    │  │ RPC bridge   │  │ (TEE→kernel events)  │  │
                    │  └──────────────┘  └──────────────────────┘  │
                    └─────────────────────────────────────────────┘
                               │ SMC / FF-A
                               ▼
┌──────────────────────────────────────────────────────────────────┐
│                  SECURE MONITOR / EL3                            │
│  ARM Trusted Firmware (ATF) / TF-A — world switch               │
└──────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────┐
│               SECURE WORLD (TrustZone / TEE OS)                  │
│  OP-TEE OS  — Trusted Applications (.ta files)                   │
│  AMD PSP firmware                                                │
└──────────────────────────────────────────────────────────────────┘
```

---

## 3. Components

### 3.1 `tee_core.c` — Core Framework

Provides the generic TEE bus and `/dev/tee<N>` / `/dev/teepriv<N>` char
devices. Backend drivers (OP-TEE, AMD TEE) register via:

```c
tee_device_alloc(desc, parent, pool, driver_data)
tee_device_register(teedev)
```

Ioctl dispatch calls into backend via `struct tee_driver_ops`:

| Op | Driver callback |
|---|---|
| `TEE_IOC_OPEN_SESSION` | `ops->open_session()` |
| `TEE_IOC_INVOKE` | `ops->invoke_func()` |
| `TEE_IOC_CANCEL` | `ops->cancel_req()` |
| `TEE_IOC_CLOSE_SESSION` | `ops->close_session()` |
| `TEE_IOC_SUPPL_RECV/SEND` | supplicant ring buffer |

### 3.2 `tee_shm.c` + `tee_shm_pool.c` — Shared Memory

TEE and the normal world must share memory for parameter passing. Three
allocation modes:

| Mode | API | Use |
|---|---|---|
| User-allocated | `TEE_IOC_SHM_ALLOC` | Kernel allocates, maps to user |
| User-registered | `TEE_IOC_SHM_REGISTER` | User provides existing pages |
| Private | `tee_shm_alloc_priv_buf()` | Kernel-only, driver internal |

Shared memory is physically contiguous (or mapped via IOMMU) so the secure
world can access it without page tables switching.

### 3.3 `optee/smc_abi.c` — TrustZone SMC ABI

The classic OP-TEE calling convention on AArch64:

1. Normal world fills parameter registers, calls `smc #0`
2. Hardware switches to EL3 (Secure Monitor)
3. ATF routes to Secure World (OP-TEE OS at S-EL1)
4. OP-TEE processes call, may issue RPC back to normal world
5. Returns via `smc #0` back to EL1 (Linux)

OP-TEE thread pool — up to N simultaneous secure threads. If all busy,
kernel call blocks until one is free.

### 3.4 `optee/ffa_abi.c` — FF-A ABI (Firmware Framework for Arm)

Newer alternative to direct SMC. Uses a **Secure Partition Manager Core
(SPMC)** at EL3 and runs OP-TEE as a Secure Partition at S-EL1. Messages
are passed as **FF-A messages** rather than raw SMC register packing.

Benefits: isolation between multiple Secure Partitions, standardized ABI.

### 3.5 `optee/rpc.c` — Secure→Normal World RPC

While processing a TEE call, OP-TEE may need normal-world services:

| RPC opcode | Purpose |
|---|---|
| `RPC_CMD_LOAD_TA` | Load a Trusted Application from filesystem |
| `RPC_CMD_FS` | Secure storage read/write (via TEE filesystem) |
| `RPC_CMD_RPMB` | Replay-Protected Memory Block access |
| `RPC_CMD_SHM_ALLOC/FREE` | Dynamic shared memory from normal world |
| `RPC_CMD_NOTIFICATION` | Async notification to normal world |

These are routed through `tee-supplicant` (userspace daemon via
`TEE_IOC_SUPPL_RECV/SEND`).

### 3.6 `optee/supp.c` — Supplicant Bridge

Ring buffer between kernel and `tee-supplicant`. When OP-TEE issues an RPC,
the kernel parks it here; supplicant picks it up, fulfills it, and returns
the result via `TEE_IOC_SUPPL_SEND`.

### 3.7 `amdtee/` — AMD TEE

Uses the AMD **Platform Security Processor (PSP)** firmware as the secure
environment. Communication via x86 mailbox (no TrustZone). Supports the
same GlobalPlatform ioctl API as OP-TEE.

### 3.8 `tstee/` — Trusted Services TEE

Bridges to Trusted Services (TF-A based Secure Partitions) via FF-A. Used
with platforms running the Trusted Services project instead of full OP-TEE.

---

## 4. Data Flow: Trusted Application Invocation

```
 libteec (userspace)                  OP-TEE OS (secure world)
 ──────────────────                   ───────────────────────
 1. TEEC_OpenSession(uuid)
    → TEE_IOC_OPEN_SESSION ioctl
         │
 2. tee_core: validate params
    build optee_msg_arg
         │
 3. optee_do_call_with_arg()
    → __arm_smccc_smc()  ──────────► 4. EL3 (ATF) world-switch
                                         │
                                     5. OP-TEE scheduler
                                        assigns thread
                                         │
                                     6. Find/load TA (UUID)
                                        → RPC: load TA binary
                                         │
 7. optee_handle_rpc()  ◄────────────── RPC_CMD_LOAD_TA
    → tee-supplicant
    → read /lib/optee_armtz/<uuid>.ta
    → map into shared mem
    → reply via SUPPL_SEND
         │
                                     8. TA initialized
                                     9. Session ID returned
                                         │
 10. Session ID → userspace ◄──────────── SMC return
         │
 11. TEEC_InvokeCommand(session, cmd, params)
     → TEE_IOC_INVOKE ioctl
     → SMC with param block
                                    12. TA cmd_entry_point(cmd, params)
                                        process in secure world
                                        return result
         │
 13. Params updated in shared mem ◄──── SMC return
     → userspace reads result
         │
 14. TEEC_CloseSession()
```

---

## 5. Key Data Structures

```c
struct tee_device {
    char name[TEE_MAX_DEV_NAME_LEN];
    const struct tee_desc *desc;
    int id;
    struct cdev cdev;            // /dev/tee<N>
    struct tee_driver_ops *ops;  // backend callbacks
    struct tee_shm_pool *pool;   // shared memory pool
    struct list_head list;
};

struct tee_context {
    struct tee_device *teedev;
    struct list_head list_shm;   // per-context shared memory list
    void *data;                  // driver-private (e.g., optee_context)
    bool cap_memref_null;        // can pass NULL memrefs
};

struct tee_shm {
    struct tee_device *teedev;
    struct tee_context *ctx;
    phys_addr_t paddr;           // physical address (for secure world)
    void *kaddr;                 // kernel virtual address
    size_t size;
    int id;                      // userspace handle
    struct dma_buf *dmabuf;      // optional dma-buf backing
};

/* GlobalPlatform parameter passed through ioctl */
struct tee_param {
    __u64 attr;                  // TEE_IOCTL_PARAM_ATTR_TYPE_*
    union {
        struct { __u64 a, b, c; } value;
        struct { __u64 shm_offs, size; __s64 shm_id; } memref;
    } u;
};
```

---

## 6. Sysfs / Devfs Layout

```
/dev/tee0       ← unprivileged access (userspace TAs, libteec)
/dev/teepriv0   ← privileged access (tee-supplicant)
/sys/bus/tee/devices/
  optee/        ← OP-TEE device (if present)
  amdtee/       ← AMD TEE (if PSP available)
/sys/class/tee/
  tee0/
  teepriv0/
```

---

## 7. Security Model

```
  Normal World                      Secure World
  ────────────                      ────────────
  Linux (EL0/EL1) ─── SMC ───► ATF (EL3) ─► OP-TEE (S-EL1)
  
  Memory protection:
  - TrustZone memory controller (TZMC) restricts secure RAM
    to secure world only — Linux cannot read it even as root
  - Shared memory is the only bridge (explicitly mapped both sides)
  
  TA isolation:
  - Each TA runs in its own process inside OP-TEE OS
  - UUIDs identify TAs; session IDs isolate concurrent clients
  - Secure storage encrypted with hardware-derived key (HUK)
```

---

## 8. Summary

The Linux TEE subsystem provides:

1. **Generic framework** (`tee_core.c`) — uniform `ioctl` API regardless of
   TEE backend (OP-TEE, AMD PSP, Trusted Services).
2. **Shared memory management** — physically accessible to both worlds without
   copying, tracked per-context.
3. **OP-TEE driver** — supports both legacy SMC ABI and modern FF-A, with full
   RPC support enabling TA loading, secure storage, and RPMB.
4. **Supplicant bridge** — clean split between kernel (policy enforcement,
   parameter marshalling) and userspace daemon (TA binary loading, filesystem).
