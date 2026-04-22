# Linux Kernel JBD2 — Journal Block Device v2

## Overview

**JBD2** (Journal Block Device, version 2) is the journaling layer used by
**ext4** (and formerly ext3). It provides ordered, atomic writes to a dedicated
journal area so that filesystem metadata remains consistent across crashes.
JBD2 lives in `fs/jbd2/` and is consumed via `include/linux/jbd2.h`.

---

## Subsystem Stack

```
┌─────────────────────────────────────────────────────────────────┐
│                        USERSPACE                                │
│   open() / write() / fsync() / rename()                        │
└──────────────────────────────┬──────────────────────────────────┘
                               │ VFS
┌──────────────────────────────▼──────────────────────────────────┐
│                       ext4 filesystem                           │
│                                                                 │
│  ext4_journal_start()   ──────────────────────────────────┐    │
│  ext4_journal_get_write_access()                          │    │
│  ext4_handle_dirty_metadata()                             │    │
│  ext4_journal_stop()    ──────────────────────────────────┘    │
└──────────────────────────────┬──────────────────────────────────┘
                               │ jbd2_* API calls
┌──────────────────────────────▼──────────────────────────────────┐
│                         JBD2 CORE                               │
│                                                                 │
│  ┌───────────────────┐    ┌──────────────────────────────────┐  │
│  │  transaction.c    │    │  commit.c                        │  │
│  │                   │    │                                  │  │
│  │  jbd2_journal_start│   │  Flushes dirty buffers to        │  │
│  │  jbd2_journal_stop│    │  journal log area.               │  │
│  │  journal_head      │    │  Writes commit block.           │  │
│  │  (one per handle)  │    │  Wakes waiters.                 │  │
│  └─────────┬─────────┘    └──────────────┬───────────────────┘  │
│            │                             │                      │
│  ┌─────────▼─────────────────────────────▼───────────────────┐  │
│  │               journal_t  (one per filesystem)              │  │
│  │                                                           │  │
│  │   j_running_transaction   ← handles attach here           │  │
│  │   j_committing_transaction ← being flushed to log          │  │
│  │   j_checkpoint_transactions ← committed, awaiting sync     │  │
│  │   j_head / j_tail          ← circular log pointers         │  │
│  │   j_transaction_sequence   ← monotonic TID counter         │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌────────────────────┐    ┌──────────────────────────────────┐  │
│  │  journal.c         │    │  checkpoint.c                   │  │
│  │                    │    │                                  │  │
│  │  kjournald2 thread │    │  Writes dirty data buffers       │  │
│  │  (commit loop)     │    │  back to filesystem, then        │  │
│  │  jbd2_log_start_   │    │  reclaims log space.             │  │
│  │    commit()        │    │                                  │  │
│  └────────────────────┘    └──────────────────────────────────┘  │
│                                                                 │
│  ┌────────────────────┐    ┌──────────────────────────────────┐  │
│  │  revoke.c          │    │  recovery.c                     │  │
│  │  Block-level revoke│    │  Replay log after crash          │  │
│  │  records prevent   │    │  (mount time only)               │  │
│  │  stale replays     │    │                                  │  │
│  └────────────────────┘    └──────────────────────────────────┘  │
└──────────────────────────────┬──────────────────────────────────┘
                               │ buffer_head / bio
┌──────────────────────────────▼──────────────────────────────────┐
│                    Block Layer  (block/)                        │
│   Journal log area  ──►  Block device  ──►  Storage            │
└─────────────────────────────────────────────────────────────────┘
```

---

## Layer-by-Layer Explanation

### 1. Journaling Modes

JBD2 supports three modes (selectable at mount time for ext4):

| Mode | What is journaled | Performance | Safety |
|---|---|---|---|
| **journal** | Data + metadata | Slowest | Highest |
| **ordered** (default) | Metadata only; data flushed before commit | Medium | High |
| **writeback** | Metadata only; data order not guaranteed | Fastest | Lower |

### 2. Handles and Transactions

```
handle_t  ──belongs to──►  transaction_t  ──belongs to──►  journal_t
 (per syscall operation)    (batched commit)                (per FS)
```

- **`handle_t`** — a short-lived object obtained by the filesystem for each
  atomic operation. Acquired with `jbd2_journal_start()`, released with
  `jbd2_journal_stop()`.
- **`transaction_t`** — groups multiple handles for a single commit. Has states:
  `T_RUNNING → T_LOCKED → T_FLUSH → T_COMMIT → T_COMMIT_DFLUSH → T_FINISHED`.
- **`journal_t`** — represents the on-disk journal. Tracks the circular log
  head/tail and serialises commits via `j_state_lock`.

### 3. journal_head vs. buffer_head

Each buffer involved in a transaction gets a `journal_head` attached to its
`buffer_head`. The journal_head tracks which transaction list the buffer is on:

| List | Meaning |
|---|---|
| `BJ_None` | Not journaled |
| `BJ_Metadata` | Modified metadata, current transaction |
| `BJ_Forget` | Will be freed after commit |
| `BJ_Shadow` | IO in-flight copy |
| `BJ_Reserved` | Reserved for fast-commit |

### 4. Commit Flow

1. `jbd2_journal_start()` — attach handle to running transaction, reserve credits.
2. `jbd2_journal_get_write_access()` — snapshot current buffer if needed (shadow).
3. Filesystem modifies buffer in memory.
4. `jbd2_journal_dirty_metadata()` — mark buffer as dirty on journal list.
5. `jbd2_journal_stop()` — decrement handle count; if zero, may trigger commit.
6. **kjournald2** thread: `jbd2_log_start_commit()` → lock transaction →
   flush dirty buffers → write descriptor blocks → write commit block.
7. Checkpoint: after commit, buffers written back to filesystem; log space reclaimed.

### 5. Fast Commit (ext4 feature)

JBD2 supports **fast commits** (`jbd2_fc_begin_commit` / `jbd2_fc_end_commit`)
for small metadata-only changes (e.g., `fsync` of a single file). Instead of a
full transaction, a compact delta is written to a reserved area at the end of
the journal, reducing fsync latency.

### 6. Recovery

At mount time, `jbd2_journal_recover()` in `recovery.c`:
1. Scans the journal log from tail to head.
2. Replays all committed but not checkpointed transactions.
3. Checks block revoke records to skip stale replays.
4. Marks journal clean; filesystem mounts normally.

---

## Commit Sequence Diagram

```
ext4                    JBD2 API             kjournald2
  │                        │                     │
  │  jbd2_journal_start()  │                     │
  │ ──────────────────────►│ alloc handle,        │
  │  (handle_t returned)   │ inc j_count          │
  │                        │                     │
  │  get_write_access()    │                     │
  │  [modify buffer]       │                     │
  │  dirty_metadata()      │                     │
  │ ──────────────────────►│ BJ_Metadata list     │
  │                        │                     │
  │  jbd2_journal_stop()   │                     │
  │ ──────────────────────►│ dec handle count     │
  │                        │ if last: signal      │
  │                        │ ────────────────────►│ wake
  │                        │                     │ lock transaction
  │                        │                     │ write descriptor blks
  │                        │                     │ write data/metadata
  │                        │                     │ write commit block
  │                        │                     │ update j_tail
  │                        │◄────────────────────│ signal waiters
  │◄───────────────────────│  fsync/barrier done  │
  │                        │                     │ checkpoint loop
  │                        │                     │ (write back to fs)
```

---

## Key Data Structures

| Structure | File | Purpose |
|---|---|---|
| `journal_t` | `include/linux/jbd2.h` | Per-journal state, log head/tail, transaction lists |
| `transaction_t` | `include/linux/jbd2.h` | One batched commit |
| `journal_head` | `include/linux/jbd2.h` | Per-buffer journal metadata |
| `handle_t` | `include/linux/jbd2.h` | Per-operation handle |
| `jbd2_inode` | `include/linux/jbd2.h` | Per-inode journal tracking |

## Key Source Files

| File | Purpose |
|---|---|
| `fs/jbd2/journal.c` | Journal lifecycle, kjournald2 thread |
| `fs/jbd2/transaction.c` | Handle/transaction management |
| `fs/jbd2/commit.c` | Commit protocol |
| `fs/jbd2/checkpoint.c` | Checkpoint and log space reclaim |
| `fs/jbd2/recovery.c` | Crash recovery / replay |
| `fs/jbd2/revoke.c` | Block revoke records |
| `include/linux/jbd2.h` | Public API and data structures |

---

## Analogy

JBD2 is like a **restaurant order pad**:

- The **chef (ext4)** writes changes on the order pad (**handle**) rather than
  directly modifying the plate.
- At the end of service (**transaction commit**), the pad is photocopied
  (**journal write**) and filed, then the changes go to the actual plates
  (**filesystem blocks**).
- If the kitchen catches fire (**crash**) before the plates are updated, the
  filed copy (**journal**) is used to reconstruct exactly what should have been
  done (**recovery**).
- Revoke records are like crossed-out orders — "ignore this old entry, it's stale".

---

## References

- `include/linux/jbd2.h` — All data structures and public API
- `Documentation/filesystems/ext4/journal.rst` — ext4 journaling design
- `fs/jbd2/` — Full implementation
