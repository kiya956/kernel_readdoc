#!/usr/bin/env python3
"""GPIO subsystem verification via chardev, sysfs, and bpftrace."""
import subprocess, os, sys, glob, tempfile, re, struct, fcntl, ctypes

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
print("  GPIO Subsystem Verification  (drivers/gpio/)")
print("=" * 64)

# ── Step 1: Prerequisites ────────────────────────────────────────────
print("\n── Step 1: Prerequisites ──────────────────────────────────────")
record("running as root", check_root())
record("bpftrace available", bpftrace_available())
record("gpiod_get symbol", symbol_exists("gpiod_get"))
record("gpiochip_add_data symbol", symbol_exists("gpiochip_add_data"))

# ── Step 2: /dev/gpiochipN enumeration ──────────────────────────────
print("\n── Step 2: /dev/gpiochipN enumeration ────────────────────────")
chips = sorted(glob.glob("/dev/gpiochip*"))
record("/dev/gpiochip* present", len(chips) > 0,
       f"{[os.path.basename(c) for c in chips]}")

# ── Step 3: GPIO_GET_CHIPINFO_IOCTL ─────────────────────────────────
print("\n── Step 3: GPIO_GET_CHIPINFO_IOCTL ───────────────────────────")
# struct gpiochip_info { char name[32]; char label[32]; __u32 lines; }
GPIO_GET_CHIPINFO_IOCTL = 0x8044B401
for chip in chips[:3]:
    try:
        fd = os.open(chip, os.O_RDWR)
        buf = bytearray(32 + 32 + 4)
        fcntl.ioctl(fd, GPIO_GET_CHIPINFO_IOCTL, buf)
        os.close(fd)
        name  = buf[:32].rstrip(b'\x00').decode(errors='replace')
        label = buf[32:64].rstrip(b'\x00').decode(errors='replace')
        lines = struct.unpack_from("<I", buf, 64)[0]
        record(f"  {os.path.basename(chip)} chip info", True,
               f"name={name!r} label={label!r} lines={lines}")
    except Exception as e:
        record(f"  {os.path.basename(chip)} chip info", False, str(e))

# ── Step 4: sysfs class/gpio ────────────────────────────────────────
print("\n── Step 4: sysfs /sys/class/gpio ─────────────────────────────")
sysfs_chips = glob.glob("/sys/class/gpio/gpiochip*")
record("sysfs gpiochip entries", len(sysfs_chips) > 0,
       f"{len(sysfs_chips)} entries")
for sc in sysfs_chips[:3]:
    n = os.path.basename(sc)
    label = sysfs_read(f"{sc}/label") or "?"
    ngpio = sysfs_read(f"{sc}/ngpio") or "?"
    base  = sysfs_read(f"{sc}/base")  or "?"
    record(f"  {n}", True, f"label={label} ngpio={ngpio} base={base}")

# ── Step 5: gpioinfo tool (optional) ────────────────────────────────
print("\n── Step 5: gpioinfo tool check ───────────────────────────────")
try:
    r = subprocess.run(["gpioinfo"], capture_output=True, text=True, timeout=5)
    lines_out = r.stdout.strip().split('\n')
    record("gpioinfo available", r.returncode == 0,
           f"{len(lines_out)} lines of output")
except FileNotFoundError:
    record("gpioinfo available", False, "not installed (apt install gpiod)")

# ── Step 6: kprobe gpiod_get ────────────────────────────────────────
print("\n── Step 6: kprobe gpiod_get ──────────────────────────────────")
if bpftrace_available() and symbol_exists("gpiod_get"):
    script = """
kprobe:gpiod_get { @gets[comm] = count(); }
interval:s:5 { print(@gets); printf("GET_DONE\\n"); exit(); }
"""
    out = run_bpftrace(script, timeout=9)
    done = "GET_DONE" in out
    record("gpiod_get kprobe compiles+runs", done)
    fired = bool(re.search(r':\s*[1-9]', out))
    record("gpiod_get fired in 5s", fired,
           "no GPIO requests in window" if not fired else "")
else:
    record("gpiod_get kprobe", False, "bpftrace or symbol missing")

# ── Step 7: kprobe gpiod_set_value ──────────────────────────────────
print("\n── Step 7: kprobe gpiod_set_value_cansleep ───────────────────")
sym = "gpiod_set_value_cansleep"
if bpftrace_available() and symbol_exists(sym):
    script = f"""
kprobe:{sym} {{ @sets[comm] = count(); }}
interval:s:5 {{ print(@sets); printf("SET_DONE\\n"); exit(); }}
"""
    out = run_bpftrace(script, timeout=9)
    record(f"{sym} kprobe compiles", "SET_DONE" in out)
else:
    record(f"{sym} kprobe", False, "bpftrace or symbol missing")

# ── Step 8: GPIO IRQ mapping symbol ─────────────────────────────────
print("\n── Step 8: GPIO IRQ symbols ──────────────────────────────────")
for sym, desc in [("gpiod_to_irq", "GPIO→IRQ mapping"),
                  ("gpiochip_irqchip_add_key", "irqchip attachment"),
                  ("gpio_irq_chip_set_chip", "IRQ chip assignment")]:
    record(f"{desc} ({sym})", symbol_exists(sym))

# ── Step 9: gpiod_get latency histogram ─────────────────────────────
print("\n── Step 9: gpiod_get latency histogram ───────────────────────")
if bpftrace_available() and symbol_exists("gpiod_get"):
    script = """
kprobe:gpiod_get    { @s[tid] = nsecs; }
kretprobe:gpiod_get {
    if (@s[tid]) { @lat_us = hist((nsecs-@s[tid])/1000); delete(@s[tid]); }
}
interval:s:5 { print(@lat_us); printf("LAT_DONE\\n"); exit(); }
"""
    out = run_bpftrace(script, timeout=9)
    record("gpiod_get latency kprobe ran", "LAT_DONE" in out)
else:
    record("gpiod_get latency", False, "bpftrace or symbol missing")

# ── Step 10: Controller driver symbols ──────────────────────────────
print("\n── Step 10: GPIO controller driver symbols ───────────────────")
for sym, desc in [("pl061_probe", "ARM PL061"),
                  ("pca953x_probe", "NXP PCA953x I²C expander"),
                  ("rockchip_gpio_probe", "Rockchip GPIO"),
                  ("msm_gpio_probe", "Qualcomm MSM GPIO"),
                  ("gpio_aggregator_probe", "GPIO aggregator")]:
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
print("  NOTE: GPIO ioctl tests require /dev/gpiochipN and root access.")
print("  kprobe results depend on active GPIO usage during the 5s window.")
print("=" * 64)
sys.exit(0 if not f else 1)
