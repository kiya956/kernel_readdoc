# nfs_common — NFS/Lockd Shared Utilities

## Overview

`fs/nfs_common/` holds **shared code** used by both the NFS client (`fs/nfs/`),
the NFS server (`fs/nfsd/`), and the lock manager (`fs/lockd/`).  It is not
a filesystem in its own right but a support library exporting kernel symbols
for:

- **Grace period management** — coordinating the "don't grant new locks yet"
  window after a server reboot (`grace.c`)
- **ACL encoding/decoding** — converting POSIX ACLs to/from the NFS wire
  format (`nfsacl.c`)
- **Server-side copy** — NFS 4.2 inter-server copy helpers (`nfs_ssc.c`)
- **Local I/O fast path** — bypassing the network stack when client and
  server run on the same kernel (`nfslocalio.c`)
- **Common XDR helpers** — shared encode/decode utilities (`common.c`)

Source: `fs/nfs_common/`

---

## Subsystem Stack

```
┌──────────────────────────────────────────────────────────────────┐
│                NFS CLIENT (fs/nfs/)                              │
│                NFS SERVER (fs/nfsd/)                             │
│                LOCK MANAGER (fs/lockd/)                          │
└───────────────────────────┬──────────────────────────────────────┘
                            │  calls shared symbols
┌───────────────────────────▼──────────────────────────────────────┐
│                   fs/nfs_common/                                 │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  grace.c — Grace Period Manager                          │   │
│  │    locks_start_grace(net, lm)  ← reboot/failover event   │   │
│  │    locks_end_grace(lm)         ← grace window over       │   │
│  │    locks_in_grace(net)         ← still in window?        │   │
│  │    locks_block_opens(lm)       ← block OPEN during grace  │   │
│  │    Per-net grace list; spinlock protected                 │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  nfsacl.c — NFS ACL XDR codec                            │   │
│  │    nfsacl_encode(xdr_buf, offset, inode, acl, encode_p)  │   │
│  │    nfsacl_decode(xdr_buf, offset, count, pacl)           │   │
│  │    Converts POSIX acl_entry ↔ NFS3 ACL wire format       │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  nfs_ssc.c — Server-Side Copy (NFS 4.2)                  │   │
│  │    nfs_ssc_register_ops()   ← server registers itself    │   │
│  │    nfs_ssc_unregister_ops()                               │   │
│  │    nfs_ssc_open_net()       ← open source file via NFS   │   │
│  │    nfs_ssc_close_net()                                    │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  nfslocalio.c — Local I/O Fast Path                      │   │
│  │    nfs_localio_enable_server()   ← co-located server     │   │
│  │    nfs_localio_disable_server()                           │   │
│  │    nfs_localio_ops (file_ops override)                    │   │
│  └──────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────┘
                            │
┌───────────────────────────▼──────────────────────────────────────┐
│               VFS / Network / net_namespace                      │
└──────────────────────────────────────────────────────────────────┘
```

---

## Grace Period State Machine

```
Server reboot / failover detected
          │
          ▼
  locks_start_grace(net, lm)
  ┌─────────────────────────────┐
  │  grace_list non-empty       │
  │  locks_in_grace() == true   │  ← new lock requests are deferred
  │  block_opens may be set     │
  └─────────────┬───────────────┘
                │  clients reclaim all locks (NFS 4.x)
                │  or timeout expires (NLM)
                ▼
  locks_end_grace(lm)
  ┌─────────────────────────────┐
  │  lm removed from grace_list │
  │  when list empty:           │
  │    locks_in_grace() == false│  ← normal locking resumes
  └─────────────────────────────┘
```

---

## Key Data Structures

| Structure | File | Purpose |
|---|---|---|
| `lock_manager` | `include/linux/lockd/lockd.h` | Per-manager grace state node |
| `grace_list` (per-net) | `grace.c` | Linked list of active grace periods |
| `nfs4_acl` | `include/linux/nfs3.h` | NFS3 ACL representation |
| `nfs_ssc_client_ops` | `include/linux/nfs_ssc.h` | Server-side copy client callbacks |
| `nfs_localio_ops` | `include/linux/nfs_localio.h` | Local I/O operation table |

---

## Key Source Files

| File | Purpose |
|---|---|
| `fs/nfs_common/grace.c` | Grace period start/end/query |
| `fs/nfs_common/nfsacl.c` | POSIX ACL ↔ NFS3 wire format |
| `fs/nfs_common/nfs_ssc.c` | NFS 4.2 server-side copy |
| `fs/nfs_common/nfslocalio.c` | Local I/O fast path |
| `fs/nfs_common/common.c` | Module init / misc helpers |
| `include/linux/lockd/lockd.h` | `lock_manager` structure |
| `include/linux/nfs_ssc.h` | SSC op table |

---

## Analogy

The grace period is like a **restaurant reopening after a power outage**:

- Before reopening (**grace period**), staff check that all prior reservations
  (held locks) are intact — no new bookings accepted yet.
- Once every prior reservation has been confirmed or expired, the restaurant
  opens normally (**grace ends**).
- The `lock_manager` is a specific maître d' (NFS or lockd); the restaurant
  only opens when *all* maîtres d' are ready.
- `nfsacl` is the hostess's translation guide: it converts the restaurant's
  internal seating-permission notation into a form guests (NFS clients)
  understand.

---

## References

- `fs/nfs_common/grace.c`
- `fs/nfs_common/nfsacl.c`
- `fs/nfs_common/nfs_ssc.c`
- RFC 5661 — NFSv4.1 (grace periods, §8)
- RFC 7862 — NFS 4.2 (server-side copy, §4.4)
