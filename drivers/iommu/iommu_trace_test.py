#!/usr/bin/env python3
"""IOMMU subsystem verification via sysfs, debugfs, and bpftrace."""
import subprocess, os, sys, glob, tempfile, re

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
print("  IOMMU Subsystem Verification  (drivers/iommu/)")
print("=" * 64)

# ── Step 1: Prerequisites ────────────────────────────────────────────
print("\n── Step 1: Prerequisites ──────────────────────────────────────")
record("running as root", check_root())
record("bpftrace available", bpftrace_available())
record("iommu_map symbol", symbol_exists("iommu_map"))
record("iommu_domain_alloc symbol", symbol_exists("iommu_domain_alloc"))

# ── Step 2: IOMMU groups in sysfs ───────────────────────────────────
print("\n── Step 2: IOMMU groups in sysfs ─────────────────────────────")
groups = sorted(glob.glob("/sys/kernel/iommu_groups/*/"))
record("IOMMU groups present", len(groups) > 0,
       f"{len(groups)} groups" if groups else "IOMMU not active (BIOS/GRUB: iommu=on)")
for g in groups[:4]:
    n = os.path.basename(g.rstrip('/'))
    devs = glob.glob(f"{g}devices/*")
    rr = sysfs_read(f"{g}reserved_regions") or ""
    record(f"  group {n}", True,
           f"{len(devs)} device(s): {[os.path.basename(d) for d in devs[:2]]}")

# ── Step 3: IOMMU dmesg presence ────────────────────────────────────
print("\n── Step 3: IOMMU kernel messages ─────────────────────────────")
try:
    r = subprocess.run(["dmesg"], capture_output=True, text=True, timeout=5)
    iommu_lines = [l for l in r.stdout.split('\n') if 'iommu' in l.lower()][:5]
    record("IOMMU messages in dmesg", len(iommu_lines) > 0,
           f"{len(iommu_lines)} lines")
    for l in iommu_lines[:3]:
        print(f"    {l.strip()}")
except Exception as e:
    record("dmesg IOMMU check", False, str(e))

# ── Step 4: debugfs iommu ───────────────────────────────────────────
print("\n── Step 4: debugfs /sys/kernel/debug/iommu ───────────────────")
iommu_dbg = "/sys/kernel/debug/iommu"
record("iommu debugfs dir", os.path.isdir(iommu_dbg), iommu_dbg)
if os.path.isdir(iommu_dbg):
    entries = os.listdir(iommu_dbg)
    record("iommu debugfs entries", len(entries) > 0, str(entries[:8]))

# ── Step 5: kprobe iommu_map ────────────────────────────────────────
print("\n── Step 5: kprobe iommu_map ──────────────────────────────────")
if bpftrace_available() and symbol_exists("iommu_map"):
    script = """
kprobe:iommu_map { @maps[comm] = count(); }
interval:s:5 { print(@maps); printf("MAP_DONE\\n"); exit(); }
"""
    out = run_bpftrace(script, timeout=9)
    done = "MAP_DONE" in out
    record("iommu_map kprobe compiles+runs", done)
    fired = bool(re.search(r':\s*[1-9]', out))
    record("iommu_map fired in 5s", fired,
           "no DMA map calls in window" if not fired else "")
else:
    record("iommu_map kprobe", False, "bpftrace or symbol missing")

# ── Step 6: iommu_map latency histogram ─────────────────────────────
print("\n── Step 6: iommu_map latency histogram ───────────────────────")
if bpftrace_available() and symbol_exists("iommu_map"):
    script = """
kprobe:iommu_map    { @s[tid] = nsecs; }
kretprobe:iommu_map {
    if (@s[tid]) { @lat_ns = hist(nsecs-@s[tid]); delete(@s[tid]); }
}
interval:s:5 { print(@lat_ns); printf("LAT_DONE\\n"); exit(); }
"""
    out = run_bpftrace(script, timeout=9)
    record("iommu_map latency kprobe ran", "LAT_DONE" in out)
else:
    record("iommu_map latency", False, "bpftrace or symbol missing")

# ── Step 7: iommu_attach_device kprobe ──────────────────────────────
print("\n── Step 7: kprobe iommu_attach_device ────────────────────────")
if bpftrace_available() and symbol_exists("iommu_attach_device"):
    script = """
kprobe:iommu_attach_device { @attaches++; }
interval:s:4 { printf("ATTACH_DONE count=%d\\n", @attaches); exit(); }
"""
    out = run_bpftrace(script, timeout=7)
    record("iommu_attach_device kprobe compiles", "ATTACH_DONE" in out)
else:
    record("iommu_attach_device kprobe", False, "bpftrace or symbol missing")

# ── Step 8: io-pgtable symbols ──────────────────────────────────────
print("\n── Step 8: io-pgtable symbols ────────────────────────────────")
for sym, desc in [("alloc_io_pgtable_ops", "io-pgtable allocator"),
                  ("free_io_pgtable_ops", "io-pgtable free"),
                  ("iommu_iova_to_phys", "IOVA→phys translation")]:
    record(f"{desc} ({sym})", symbol_exists(sym))

# ── Step 9: IOVA allocator symbols ──────────────────────────────────
print("\n── Step 9: IOVA allocator symbols ────────────────────────────")
for sym, desc in [("iova_domain_init_rcaches", "IOVA domain init"),
                  ("alloc_iova_fast", "fast IOVA alloc"),
                  ("free_iova_fast", "fast IOVA free")]:
    record(f"{desc} ({sym})", symbol_exists(sym))

# ── Step 10: HW driver symbols ──────────────────────────────────────
print("\n── Step 10: IOMMU hardware driver symbols ────────────────────")
for sym, desc in [("intel_iommu_init", "Intel VT-d"),
                  ("amd_iommu_init", "AMD IOMMU"),
                  ("arm_smmu_probe", "ARM SMMUv2"),
                  ("arm_smmu_v3_probe", "ARM SMMUv3"),
                  ("apple_dart_probe", "Apple DART")]:
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
print("  NOTE: IOMMU must be enabled in BIOS and kernel cmdline (intel_iommu=on")
print("  or iommu=pt). Without it, iommu_groups will be empty.")
print("=" * 64)
sys.exit(0 if not f else 1)
