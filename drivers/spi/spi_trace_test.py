#!/usr/bin/env python3
"""SPI subsystem verification via sysfs, /dev/spidevN.M, and bpftrace."""
import subprocess, os, sys, glob, tempfile, re, struct, fcntl, array

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
print("  SPI Subsystem Verification  (drivers/spi/)")
print("=" * 64)

# ── Step 1: Prerequisites ────────────────────────────────────────────
print("\n── Step 1: Prerequisites ──────────────────────────────────────")
record("running as root", check_root())
record("bpftrace available", bpftrace_available())
record("spi_sync symbol", symbol_exists("spi_sync"))
record("spi_async symbol", symbol_exists("spi_async"))

# ── Step 2: SPI controller enumeration ──────────────────────────────
print("\n── Step 2: SPI controller enumeration ────────────────────────")
spi_devs = glob.glob("/sys/bus/spi/devices/spi*")
controllers = set()
for d in spi_devs:
    m = re.match(r'spi(\d+)', os.path.basename(d))
    if m: controllers.add(m.group(1))
record("SPI controllers present", len(controllers) > 0,
       f"controllers: {sorted(controllers)}" if controllers else "none")
record("SPI devices in sysfs", len(spi_devs) > 0,
       f"{[os.path.basename(d) for d in spi_devs[:6]]}")

# ── Step 3: /dev/spidevN.M character devices ─────────────────────────
print("\n── Step 3: /dev/spidevN.M character devices ─────────────────")
spidev_nodes = glob.glob("/dev/spidev*")
record("/dev/spidev* present", len(spidev_nodes) > 0,
       f"{[os.path.basename(d) for d in spidev_nodes]}" if spidev_nodes
       else "spidev not loaded or no spi devices configured")

# ── Step 4: spidev ioctl SPI_IOC_RD_MODE ────────────────────────────
print("\n── Step 4: spidev SPI_IOC_RD_MODE ioctl ─────────────────────")
SPI_IOC_RD_MODE = 0x80016B01  # _IOR(SPI_IOC_MAGIC, 1, __u8)
if spidev_nodes and check_root():
    dev = spidev_nodes[0]
    try:
        fd = os.open(dev, os.O_RDWR)
        buf = bytearray(1)
        fcntl.ioctl(fd, SPI_IOC_RD_MODE, buf)
        os.close(fd)
        record(f"SPI_IOC_RD_MODE on {os.path.basename(dev)}", True,
               f"mode=0x{buf[0]:02x} (CPOL={buf[0]>>1&1} CPHA={buf[0]&1})")
    except Exception as e:
        record(f"SPI_IOC_RD_MODE on {os.path.basename(dev)}", False, str(e))
else:
    record("SPI_IOC_RD_MODE", False, "no spidev node or not root")

# ── Step 5: sysfs device attributes ─────────────────────────────────
print("\n── Step 5: sysfs device attributes ──────────────────────────")
for d in spi_devs[:3]:
    n = os.path.basename(d)
    for attr in ["modalias", "driver"]:
        p = f"{d}/{attr}"
        if os.path.islink(p):
            record(f"  {n}/driver", True, os.path.basename(os.readlink(p)))
        elif os.path.exists(p):
            v = sysfs_read(p)
            record(f"  {n}/{attr}", v is not None, v or "?")

# ── Step 6: kprobe spi_sync ─────────────────────────────────────────
print("\n── Step 6: kprobe spi_sync ────────────────────────────────────")
if bpftrace_available() and symbol_exists("spi_sync"):
    script = """
kprobe:spi_sync { @spi_syncs[comm] = count(); }
interval:s:5 { print(@spi_syncs); printf("SYNC_DONE\\n"); exit(); }
"""
    out = run_bpftrace(script, timeout=9)
    done = "SYNC_DONE" in out
    record("spi_sync kprobe compiles+runs", done)
    fired = bool(re.search(r':\s*[1-9]', out))
    record("spi_sync fired in 5s", fired,
           "no SPI transfers in window" if not fired else "")
else:
    record("spi_sync kprobe", False, "bpftrace or symbol missing")

# ── Step 7: Transfer latency histogram ──────────────────────────────
print("\n── Step 7: spi_sync latency histogram ────────────────────────")
if bpftrace_available() and symbol_exists("spi_sync"):
    script = """
kprobe:spi_sync    { @s[tid] = nsecs; }
kretprobe:spi_sync {
    if (@s[tid]) { @lat_us = hist((nsecs-@s[tid])/1000); delete(@s[tid]); }
}
interval:s:5 { print(@lat_us); printf("LAT_DONE\\n"); exit(); }
"""
    out = run_bpftrace(script, timeout=9)
    record("spi_sync latency kprobe ran", "LAT_DONE" in out)
else:
    record("spi_sync latency", False, "bpftrace or symbol missing")

# ── Step 8: spi_async kprobe ────────────────────────────────────────
print("\n── Step 8: spi_async kprobe ───────────────────────────────────")
if bpftrace_available() and symbol_exists("spi_async"):
    script = """
kprobe:spi_async { @async_count++; }
interval:s:4 { printf("ASYNC_DONE count=%d\\n", @async_count); exit(); }
"""
    out = run_bpftrace(script, timeout=7)
    record("spi_async kprobe compiles", "ASYNC_DONE" in out)
    m = re.search(r'count=(\d+)', out)
    if m:
        record("spi_async transfers observed", int(m.group(1)) > 0,
               f"count={m.group(1)}")
else:
    record("spi_async kprobe", False, "bpftrace or symbol missing")

# ── Step 9: spi-mem symbols ──────────────────────────────────────────
print("\n── Step 9: spi-mem symbols ────────────────────────────────────")
for sym, desc in [("spi_mem_exec_op", "spi-mem exec op"),
                  ("spi_mem_supports_op", "spi-mem op capability check"),
                  ("spi_mem_adjust_op_size", "spi-mem op size adjustment")]:
    record(f"{desc} ({sym})", symbol_exists(sym))

# ── Step 10: Controller driver symbols ──────────────────────────────
print("\n── Step 10: Controller driver symbols ────────────────────────")
for sym, desc in [("dw_spi_probe", "Synopsys DesignWare SPI"),
                  ("spi_geni_probe", "Qualcomm GENI SPI"),
                  ("bcm2835_spi_probe", "Broadcom BCM2835"),
                  ("omap2_mcspi_probe", "OMAP McSPI"),
                  ("spi_intel_probe", "Intel SPI")]:
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
print("  NOTE: spidev requires CONFIG_SPI_SPIDEV=y and a DT/ACPI node.")
print("  Controller symbols depend on loaded modules for that SoC.")
print("=" * 64)
sys.exit(0 if not f else 1)
