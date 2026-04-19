# Linux Kernel: iommu — IOMMU Subsystem

> Source: `drivers/iommu/` — noble-linux-oem (oem-6.17-next)

---

## 1. What is the IOMMU subsystem?

An **IOMMU** (Input/Output Memory Management Unit) is a hardware unit that
translates device DMA addresses (I/O Virtual Addresses, IOVAs) to physical
memory addresses, enforcing access control. The Linux IOMMU subsystem
provides:

- **DMA isolation**: devices can only access memory they are explicitly
  mapped for — prevents rogue DMA attacks
- **VFIO / device passthrough**: guests use IOVAs that map to guest memory
- **IOVA allocator**: efficient virtual address management per domain
- **io-pgtable**: architecture-agnostic page table library (ARM SMMU, Intel
  VT-d, AMD IOMMU all share common page table code)
- **Fault handling**: report page faults to userspace via SVA or io-pgfault

---

## 2. Subsystem Stack

```
┌──────────────────────────────────────────────────────────────────────┐
│  CONSUMERS                                                           │
│  DMA-API (dma_map_single, dma_alloc_coherent) → iommu domain        │
│  VFIO: vfio_iommu_type1 → iommu_map() / iommu_unmap()              │
│  SVA: iommu_sva_bind_device() → PASID table entry                  │
└──────────────────────────┬───────────────────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────────────────┐
│  IOMMU CORE  (iommu.c)                                               │
│  iommu_domain: type (DMA / IDENTITY / UNMANAGED / SVA)              │
│  iommu_attach_device(domain, dev)                                   │
│  iommu_map(domain, iova, paddr, size, prot)                         │
│  iommu_unmap(domain, iova, size)                                    │
│  iommu_iova_to_phys(domain, iova) → physical addr                  │
│  DMA-IOMMU layer (dma-iommu.c): DMA-API ↔ iommu_domain bridge     │
│  IOVA allocator (iova.c): red-black tree of free IOVA ranges        │
└──────────────────────────┬───────────────────────────────────────────┘
                           │ iommu_ops
                           ▼
┌──────────────────────────────────────────────────────────────────────┐
│  io-pgtable  (io-pgtable-arm.c / io-pgtable-arm-v7s.c / dart.c)    │
│  Shared page-table library:                                          │
│    alloc_io_pgtable_ops(fmt, cfg, cookie)                           │
│    ops->map_pages(ops, iova, paddr, pgsize, pgcount, prot, gfp, …) │
│    ops->unmap_pages(…)                                              │
│    ops->iova_to_phys(ops, iova)                                     │
└──────────────────────────┬───────────────────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────────────────┐
│  IOMMU HARDWARE DRIVERS                                              │
│  intel/iommu.c  — Intel VT-d (DMAR tables, context entries, PASID)  │
│  amd/iommu.c    — AMD IOMMU (IVRS tables, device tables, PPR)       │
│  arm/arm-smmu.c — ARM SMMUv1/v2 (stream mapping records)           │
│  arm/arm-smmu-v3.c — ARM SMMUv3 (2-stage, SVA, MPAM)              │
│  apple-dart.c   — Apple DART (DMA Address Remap Table)             │
│  exynos-iommu.c — Samsung Exynos SYSMMU                            │
│  qcom_iommu.c   — Qualcomm IOMMU                                   │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 3. Key Data Structures

| Structure | Role |
|---|---|
| `struct iommu_ops` | Driver vtable: `domain_alloc`, `attach_dev`, `map_pages`, `unmap_pages`, `iova_to_phys` |
| `struct iommu_domain` | One DMA address space; contains page tables, iova allocator, type |
| `struct iommu_group` | Group of devices that share the same IOMMU context (typically one PCIe function) |
| `struct iommu_fwspec` | Firmware info (IOMMU handle, StreamID list) parsed from DT/ACPI |
| `struct io_pgtable_ops` | Page-table vtable: `map_pages`, `unmap_pages`, `iova_to_phys` |
| `struct iova_domain` | Per-domain IOVA red-black tree + per-CPU cache |

---

## 4. Key API

### Core consumer/mapping

```c
/* Allocate a domain for a device's group */
struct iommu_domain *iommu_domain_alloc(struct bus_type *bus);
void iommu_domain_free(struct iommu_domain *domain);

/* Attach / detach */
int iommu_attach_device(struct iommu_domain *domain, struct device *dev);
void iommu_detach_device(struct iommu_domain *domain, struct device *dev);

/* Map / unmap */
int iommu_map(struct iommu_domain *domain, unsigned long iova,
              phys_addr_t paddr, size_t size, int prot, gfp_t gfp);
size_t iommu_unmap(struct iommu_domain *domain, unsigned long iova, size_t size);

/* Translate */
phys_addr_t iommu_iova_to_phys(struct iommu_domain *domain, dma_addr_t iova);
```

### IOMMU group management

```c
struct iommu_group *iommu_group_get(struct device *dev);
int iommu_group_id(struct iommu_group *group);
```

### SVA (Shared Virtual Addressing)

```c
struct iommu_sva *iommu_sva_bind_device(struct device *dev, struct mm_struct *mm);
void iommu_sva_unbind_device(struct iommu_sva *handle);
u32 iommu_sva_get_pasid(struct iommu_sva *handle);
```

---

## 5. Data-Flow: DMA-API → IOMMU

```
Driver: dma_map_single(dev, vaddr, len, DMA_TO_DEVICE)
        │
        ▼
dma_iommu_map_sg() / iommu_dma_map_sg()
        │
        ├─ iova_alloc(domain->iovad, size, align) → IOVA
        │
        ├─ iommu_map(domain, iova, phys, size, IOMMU_READ)
        │      └─ ops->map_pages(domain, iova, phys, pgsize, …)
        │             └─ io-pgtable: write PTEs into IOMMU page table
        │
        └─ returns dma_addr_t (= IOVA) to driver

Driver programs device DMA with IOVA
        │
IOMMU HW translates IOVA → phys on every memory access
        └─ page fault → iommu_report_device_fault() if not mapped
```

---

## 6. Intel VT-d Architecture

```
DMAR ACPI table → scope: which PCI segments use VT-d
        │
        ▼
DRHD (DMA Remapping HW Units)
        │
        ├─ Root Table → Context Table → Second-Level Page Tables
        │         (per PCI bus/dev/func → per IOMMU domain)
        │
        └─ PASID Table (SVA): per-process page tables via ENQCMD
```

---

## 7. sysfs / debugfs Layout

```
/sys/kernel/iommu_groups/
    <N>/
        devices/          ← symlinks to PCI devices in this group
        reserved_regions  ← identity-mapped ranges

/sys/kernel/debug/iommu/
    domain_list           ← all active IOMMU domains

/sys/kernel/debug/iommu/intel/
    dmar_translation_struct  ← VT-d context tables
    ir_translation_struct    ← interrupt remapping tables
```

---

## 8. Summary

The IOMMU subsystem's layering (core → io-pgtable → HW driver) allows the
same DMA-API, VFIO, and SVA code to run on Intel VT-d, AMD IOMMU, and all
ARM SMMU variants. The shared **io-pgtable** library means page-table
management bugs are fixed once for all platforms, while each hardware driver
only implements the `iommu_ops` vtable for its specific context/stream table
format.
