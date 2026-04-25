# Stream Parser Subsystem (`net/strparser`)

## Overview

The stream parser (`strparser`) is a generic kernel framework for detecting
application-layer message boundaries within TCP byte streams. It uses a BPF
program (or callback) to parse incoming stream data and extract individual
messages, enabling protocols like TLS (kTLS) and KCM (Kernel Connection
Multiplexor) to receive complete, framed messages from the kernel.

Without strparser, every stream-based protocol must implement its own
partial-read / reassembly logic. Strparser centralizes this: you supply a
parser that returns the message length, and strparser handles accumulation,
delivery, and error recovery.

## Architecture

```
┌───────────────────────────────────────────────────┐
│            Consumer (kTLS / KCM / etc.)           │
│         (receives complete framed messages)        │
├───────────────────────────────────────────────────┤
│                  strparser Core                    │
│  ┌─────────────────────────────────────────────┐  │
│  │  struct strparser                           │  │
│  │  ┌──────────────┐  ┌────────────────────┐   │  │
│  │  │ parse_msg()  │  │ rcv_msg() callback │   │  │
│  │  │ (BPF / cb)   │  │ (deliver to user)  │   │  │
│  │  └──────┬───────┘  └────────┬───────────┘   │  │
│  │         │                   │               │  │
│  │    ┌────▼───────────────────▼────────┐      │  │
│  │    │     skb accumulation buffer     │      │  │
│  │    │  (partial msg reassembly)       │      │  │
│  │    └────────────────────────────────┘       │  │
│  └─────────────────────────────────────────────┘  │
├───────────────────────────────────────────────────┤
│              TCP Socket (stream data)              │
│           (sk->sk_data_ready → strp)               │
└───────────────────────────────────────────────────┘
```

## Message Parsing Workflow

```
  TCP data arrives
       │
       ▼
  strp_data_ready()
       │
       ▼
  ┌──────────────────┐
  │  parse_msg(skb)  │──── returns message length
  └────────┬─────────┘
           │
     ┌─────▼──────┐
     │ length > 0? │
     ├─── YES ─────┼──── enough data? ──┐
     │             │                    │
     │         YES ▼                NO  ▼
     │    rcv_msg(skb)         accumulate, wait
     │    (deliver msg)        for more data
     │             │                    │
     │             ▼                    │
     │    parse next msg ◄──────────────┘
     │                                  
     ├─── NO (0) ──► need more data     
     │                                  
     └─── ERROR ───► strp_abort()       
```

## Key Structures

| Structure          | Description                                                 |
|--------------------|-------------------------------------------------------------|
| `struct strparser` | Per-socket parser instance — state machine, skb head, callbacks |
| `struct strp_msg`  | Message metadata — offset and length within skb             |
| `struct strp_callbacks` | Consumer-supplied callbacks (parse_msg, rcv_msg, etc.) |

## Key Functions

| Function              | Description                                          |
|-----------------------|------------------------------------------------------|
| `strp_data_ready()`   | Entry point: called when TCP socket has data ready   |
| `strp_process()`      | Core loop: parse and deliver messages from stream    |
| `strp_init()`         | Initialize a strparser instance with callbacks       |
| `strp_stop()`         | Stop parsing on a socket                             |
| `strp_done()`         | Destroy strparser and free resources                 |
| `strp_check_rcv()`    | Re-check socket for pending data                     |

## Analogy

Think of strparser as a **mail room clerk** who receives a continuous roll of
paper from a fax machine (TCP stream). The clerk has a ruler (BPF parse
program) that measures where each letter ends. Once a complete letter is
measured, the clerk cuts it out and delivers it to the right desk (consumer).
If the fax stops mid-letter, the clerk waits for more paper before cutting.
Without the clerk, every desk would need its own scissors and partial-letter
storage.

## Source Files

| File                    | Purpose                              |
|-------------------------|--------------------------------------|
| `net/strparser/strparser.c` | Core stream parser implementation |
| `include/net/strparser.h`   | Public API and structures         |
