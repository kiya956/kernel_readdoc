#!/usr/bin/env python3
"""
test_netlabel_subsystem.py — bpftrace verification of NetLabel.

Steps
-----
1.  Check netlabel Netlink family exists
2.  Probe netlbl_skbuff_getattr        — read label from incoming packet
3.  Probe netlbl_skbuff_setattr        — write label to outgoing packet
4.  Probe netlbl_cfg_map_del           — delete domain mapping
5.  Probe netlbl_cfg_unlbl_map_add     — add unlabeled domain mapping
6.  Probe netlbl_domhsh_search_def     — domain hash table lookup
7.  Probe cipso_v4_optptr             — CIPSOv4 option processing
8.  Probe netlbl_catmap_setbit         — category bitmap set
9.  Check /proc/net/netlabel directory
10. Check CONFIG_NETLABEL in kernel config
"""

import subprocess
import sys
import os
import time
import tempfile
import socket

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
SKIP = "\033[33mSKIP\033[0m"

BPFTRACE = "/usr/bin/bpftrace"
ATTACH_WAIT = 2.0


def run_bpftrace(program: str, trigger=None, timeout: int = 10) -> tuple[str, str, bool]:
    with tempfile.NamedTemporaryFile("w", suffix=".bt", delete=False) as f:
        f.write(program)
        bt_file = f.name
    try:
        proc = subprocess.Popen(
            [BPFTRACE, bt_file],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        time.sleep(ATTACH_WAIT)
        if trigger:
            try:
                trigger()
            except Exception:
                pass
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
        skipped = any(
            kw in stderr for kw in ("not traceable", "No probes", "unrecognized")
        )
        return stdout, stderr, skipped
    finally:
        os.unlink(bt_file)


results = []


def check(step_num: int, name: str, program: str, trigger=None,
          expect: str = "HIT", timeout: int = 10):
    stdout, stderr, skipped = run_bpftrace(program, trigger, timeout)
    if skipped:
        results.append((step_num, name, SKIP))
        print(f"  Step {step_num:2d}: {name:52s} {SKIP}")
        return
    ok = expect in stdout
    status = PASS if ok else FAIL
    results.append((step_num, name, status))
    print(f"  Step {step_num:2d}: {name:52s} {status}")
    if not ok:
        if stdout.strip():
            print(f"            stdout: {stdout.strip()[:200]}")
        if stderr.strip():
            print(f"            stderr: {stderr.strip()[:200]}")


print("\n=== netlabel subsystem bpftrace verification ===\n")


def net_trigger():
    """Trigger network I/O to exercise netlabel paths."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.2)
        s.connect(("127.0.0.1", 12345))
        s.send(b"netlabel test")
        s.close()
    except Exception:
        pass


# ── Step 1: netlabel Netlink family ──────────────────────────────────────────
print(f"  Step  1: {'netlabel Netlink family detectable':52s}", end=" ")
netlabel_present = False
try:
    with open("/proc/kallsyms") as f:
        for line in f:
            if "netlbl_skbuff_getattr" in line:
                netlabel_present = True
                break
except Exception:
    pass
if netlabel_present:
    print(PASS)
    results.append((1, "netlabel kallsyms", PASS))
else:
    print(SKIP)
    results.append((1, "netlabel kallsyms", SKIP))

# ── Step 2: netlbl_skbuff_getattr ────────────────────────────────────────────
prog2 = """
kprobe:netlbl_skbuff_getattr {
    printf("HIT netlbl_skbuff_getattr\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(2, "netlbl_skbuff_getattr kprobe", prog2, trigger=net_trigger, timeout=10)

# ── Step 3: netlbl_skbuff_setattr ────────────────────────────────────────────
prog3 = """
kprobe:netlbl_skbuff_setattr {
    printf("HIT netlbl_skbuff_setattr\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(3, "netlbl_skbuff_setattr kprobe", prog3, trigger=net_trigger, timeout=10)

# ── Step 4: netlbl_cfg_map_del ───────────────────────────────────────────────
prog4 = """
kprobe:netlbl_cfg_map_del {
    printf("HIT netlbl_cfg_map_del\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(4, "netlbl_cfg_map_del kprobe", prog4, timeout=8)

# ── Step 5: netlbl_cfg_unlbl_map_add ─────────────────────────────────────────
prog5 = """
kprobe:netlbl_cfg_unlbl_map_add {
    printf("HIT netlbl_cfg_unlbl_map_add\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(5, "netlbl_cfg_unlbl_map_add kprobe", prog5, timeout=8)

# ── Step 6: netlbl_domhsh_search_def ─────────────────────────────────────────
prog6 = """
kprobe:netlbl_domhsh_search_def {
    printf("HIT netlbl_domhsh_search_def\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(6, "netlbl_domhsh_search_def kprobe", prog6,
      trigger=net_trigger, timeout=10)

# ── Step 7: cipso_v4_optptr ──────────────────────────────────────────────────
prog7 = """
kprobe:cipso_v4_optptr {
    printf("HIT cipso_v4_optptr\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(7, "cipso_v4_optptr kprobe", prog7, trigger=net_trigger, timeout=10)

# ── Step 8: netlbl_catmap_setbit ─────────────────────────────────────────────
prog8 = """
kprobe:netlbl_catmap_setbit {
    printf("HIT netlbl_catmap_setbit\\n");
    exit();
}
interval:s:5 { exit(); }
"""
check(8, "netlbl_catmap_setbit kprobe", prog8, timeout=8)

# ── Step 9: /proc/net/netlabel ───────────────────────────────────────────────
print(f"  Step  9: {'/proc/net/netlabel/ directory':52s}", end=" ")
# netlabel doesn't always create /proc/net/netlabel; check for related files
netlabel_proc = any(
    os.path.exists(p) for p in [
        "/proc/net/netlabel",
        "/proc/net/cipso",
        "/proc/net/netlabel/version",
    ]
)
if netlabel_proc:
    print(PASS)
    results.append((9, "netlabel procfs", PASS))
else:
    print(SKIP)
    results.append((9, "netlabel procfs", SKIP))

# ── Step 10: CONFIG_NETLABEL ─────────────────────────────────────────────────
print(f"  Step 10: {'CONFIG_NETLABEL in kernel config':52s}", end=" ")
configured = False
config_files = ["/proc/config.gz", "/boot/config-" + os.uname().release]
for cf in config_files:
    if not os.path.exists(cf):
        continue
    try:
        if cf.endswith(".gz"):
            import gzip
            data = gzip.open(cf, "rt").read()
        else:
            data = open(cf).read()
        if "CONFIG_NETLABEL=y" in data or "CONFIG_NETLABEL=m" in data:
            configured = True
            break
    except Exception:
        pass
if configured:
    print(PASS)
    results.append((10, "CONFIG_NETLABEL", PASS))
else:
    print(SKIP)
    results.append((10, "CONFIG_NETLABEL", SKIP))

# ── Summary ──────────────────────────────────────────────────────────────────
print()
passed = sum(1 for _, _, s in results if s == PASS)
failed = sum(1 for _, _, s in results if s == FAIL)
skipped = sum(1 for _, _, s in results if s == SKIP)
total = len(results)
print(f"Results: {passed}/{total} passed, {failed} failed, {skipped} skipped")
if failed > 0:
    sys.exit(1)
