#!/usr/bin/env python3
"""I²C subsystem verification via sysfs, /dev/i2c-N, and bpftrace."""
import subprocess, os, sys, glob, tempfile, re, struct, fcntl

PASS="\033[32mPASS\033[0m"; FAIL="\033[31mFAIL\033[0m"
results=[]

def record(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))

def check_root(): return os.geteuid() == 0
def bpftrace_available():
    try: return subprocess.run(["bpftrace","--version"], capture_output=True, timeout=5).returncode == 0
    except: return False

def symbol_exists(sym):
    try:
        r = subprocess.run(["grep","-wc",sym,"/proc/kallsyms"], capture_output=True, text=True, timeout=5)
        return r.returncode == 0 and int(r.stdout.strip()) > 0
    except: return False

def run_bpftrace(script, timeout=9):
    with tempfile.NamedTemporaryFile("w", suffix=".bt", delete=False) as f:
        f.write(script); fname = f.name
    try:
        r = subprocess.run(["bpftrace", fname], capture_output=True, text=True, timeout=timeout)
        return r.stdout + r.stderr
    except subprocess.TimeoutExpired: return ""
    finally: os.unlink(fname)

def sysfs_read(p):
    try:
        with open(p) as f: return f.read().strip()
    except: return None

print("=" * 64)
print("  I²C Subsystem Verification  (drivers/i2c/)")
print("=" * 64)

# ── Step 1: Prerequisites ────────────────────────────────────────────
print("\n── Step 1: Prerequisites ──────────────────────────────────────")
record("running as root", check_root())
record("bpftrace available", bpftrace_available())
record("i2c-core symbol", symbol_exists("i2c_transfer"))

# ── Step 2: Adapter enumeration ─────────────────────────────────────
print("\n── Step 2: Adapter enumeration ─────────────────────────────────")
adapters = glob.glob("/sys/class/i2c-adapter/i2c-*")
record("i2c adapters present", len(adapters) > 0,
       f"{len(adapters)} adapters" if adapters else "none")
for adap in adapters[:3]:
    name = sysfs_read(f"{adap}/name") or "?"
    bn = os.path.basename(adap)
    record(f"  {bn} name", True, name)

# ── Step 3: /dev/i2c-N chardev ──────────────────────────────────────
print("\n── Step 3: /dev/i2c-N character devices ─────────────────────")
i2c_devs = glob.glob("/dev/i2c-*")
record("/dev/i2c-* present", len(i2c_devs) > 0,
       f"{[os.path.basename(d) for d in sorted(i2c_devs)[:6]]}")

# ── Step 4: I2C_FUNCS ioctl ─────────────────────────────────────────
print("\n── Step 4: I2C_FUNCS ioctl on first adapter ─────────────────")
I2C_FUNCS = 0x0705
if i2c_devs and check_root():
    dev = sorted(i2c_devs)[0]
    try:
        fd = os.open(dev, os.O_RDWR)
        buf = bytearray(8)
        fcntl.ioctl(fd, I2C_FUNCS, buf)
        os.close(fd)
        funcs = struct.unpack_from("<Q", buf)[0]
        has_i2c   = bool(funcs & 0x1)
        has_smbus = bool(funcs & 0x1_4000)
        record(f"I2C_FUNCS on {os.path.basename(dev)}",
               has_i2c or has_smbus,
               f"funcs=0x{funcs:08x} i2c={has_i2c} smbus={has_smbus}")
    except Exception as e:
        record(f"I2C_FUNCS on {os.path.basename(dev)}", False, str(e))
else:
    record("I2C_FUNCS ioctl", False, "no /dev/i2c-* or not root")

# ── Step 5: Client devices in sysfs ─────────────────────────────────
print("\n── Step 5: Client devices in sysfs ──────────────────────────")
clients = glob.glob("/sys/bus/i2c/devices/*/name")
record("i2c clients in sysfs", len(clients) > 0,
       f"{len(clients)} client(s)" if clients else "none")
for c in clients[:4]:
    v = sysfs_read(c)
    record(f"  {os.path.basename(os.path.dirname(c))}", v is not None, v or "?")

# ── Step 6: kprobe i2c_transfer ─────────────────────────────────────
print("\n── Step 6: kprobe i2c_transfer ───────────────────────────────")
if bpftrace_available() and symbol_exists("i2c_transfer"):
    script = """
kprobe:i2c_transfer { @xfers[comm] = count(); }
interval:s:5 { print(@xfers); printf("XFER_DONE\\n"); exit(); }
"""
    out = run_bpftrace(script, timeout=9)
    done = "XFER_DONE" in out
    record("i2c_transfer kprobe compiles+runs", done)
    fired = bool(re.search(r':\s*[1-9]', out)) and "xfer" in out.lower()
    record("i2c_transfer fired in 5s", fired,
           "no transfers in window" if not fired else "")
else:
    record("i2c_transfer kprobe", False, "bpftrace or symbol missing")

# ── Step 7: Transfer latency histogram ──────────────────────────────
print("\n── Step 7: i2c_transfer latency histogram ────────────────────")
if bpftrace_available() and symbol_exists("i2c_transfer"):
    script = """
kprobe:i2c_transfer    { @s[tid] = nsecs; }
kretprobe:i2c_transfer {
    if (@s[tid]) { @lat_us = hist((nsecs-@s[tid])/1000); delete(@s[tid]); }
}
interval:s:5 { print(@lat_us); printf("LAT_DONE\\n"); exit(); }
"""
    out = run_bpftrace(script, timeout=9)
    record("i2c_transfer latency kprobe ran", "LAT_DONE" in out)
else:
    record("i2c_transfer latency", False, "bpftrace or symbol missing")

# ── Step 8: i2c_add_adapter kprobe ──────────────────────────────────
print("\n── Step 8: i2c_add_adapter kprobe ────────────────────────────")
if bpftrace_available() and symbol_exists("i2c_add_adapter"):
    script = """
kprobe:i2c_add_adapter { @add++; }
interval:s:4 { printf("ADD_DONE\\n"); exit(); }
"""
    out = run_bpftrace(script, timeout=7)
    record("i2c_add_adapter kprobe compiles", "ADD_DONE" in out)
else:
    record("i2c_add_adapter kprobe", False, "bpftrace or symbol missing")

# ── Step 9: SMBus emulation symbol ──────────────────────────────────
print("\n── Step 9: SMBus emulation symbols ───────────────────────────")
for sym in ["i2c_smbus_xfer", "i2c_smbus_read_byte_data",
            "__i2c_smbus_xfer"]:
    record(f"symbol {sym}", symbol_exists(sym))

# ── Step 10: I²C mux symbols ────────────────────────────────────────
print("\n── Step 10: I²C mux/ATR symbols ─────────────────────────────")
for sym, desc in [("i2c_mux_add_adapter", "i2c-mux core"),
                  ("i2c_atr_add_adapter", "i2c-atr (address translator)"),
                  ("i2c_new_client_device", "dynamic client registration")]:
    record(f"{desc} ({sym})", symbol_exists(sym))

print("\n" + "=" * 64)
print("  SUMMARY")
print("=" * 64)
p = sum(1 for _, ok, _ in results if ok)
f = len(results) - p
print(f"  PASS: {p}/{len(results)}   FAIL: {f}/{len(results)}")
if f:
    for n, ok, d in results:
        if not ok: print(f"    - {n}" + (f": {d}" if d else ""))
print("  NOTE: I²C transfers need devices on the bus; idle systems may")
print("  show 0 transfers in the 5-second kprobe window.")
print("=" * 64)
sys.exit(0 if not f else 1)
