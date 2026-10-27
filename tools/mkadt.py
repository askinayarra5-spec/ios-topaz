#!/usr/bin/env python3
"""Build an Apple Device Tree for TOPAZ or an experimental A50 boot.

The default path retains TOPAZ's existing values. The A50 path requires a raw
stock FDT and derives RAM, CPUs, GIC and timer values from it.
"""
import argparse
import hashlib
import os
import struct
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adt  # noqa: E402
from a50_fdt import (  # noqa: E402
    cell_value, cells, integer, memory_ranges, node_cells, parse_fdt,
    reg_ranges, reserved_memory_ranges, string_list, memreserve_ranges,
)

# Kernel-managed RAM: above every Qualcomm XPU carve-out (see NOTES.md)
PHYS_BASE = 0xb0000000
MEM_SIZE = 0x4d000000
# --highmem (phone only): the whole upper DDR bank. The topaz live FDT memory
# node is <0x40000000 0x3bb00000>, <0x80000000 3G>, <0x1_40000000 4G>; every
# fixed reserved-memory carve-out is below 0x63900000 and the dynamic pools are
# limited below 4G.
HIGH_PHYS_BASE = 0x140000000
HIGH_MEM_SIZE = 0x100000000
# --midmem (phone only): the middle and upper banks as one range, 0x80000000 +
# 3G + 4G = 7G up to 0x2_40000000. No fixed carve-out is above 0x63900000, and it
# gives the kernel RAM below 4 GiB, which the GPU needs (it does not reach above).
MID_PHYS_BASE = int(__import__("os").environ.get("MIDBASE", "0x80000000"), 16)
MID_MEM_SIZE = int(__import__("os").environ.get("MIDSIZE", "0x1c0000000"), 16)
# /arm-io children are relative to this; it must not be 0 (serial_init bails)
SOC_BASE = 0x01000000

# interrupt-controller@f200000 (redistributor-stride 0x20000, 8 cores)
GICD, GICD_SIZE = 0x0f200000, 0x10000
GICR, GICR_SIZE = 0x0f300000, 0x100000
# cont_splash_region left lit by ABL
FB, FB_SIZE = 0x5c000000, 0xf00000
SWEET_ECID = 0x544f50415a000001  # 'TOPAZ' + 1, synthetic
FB_W, FB_H, FB_STRIDE = 1080, 2400, 1080 * 4
# Internal UFS host (ufshc@4804000 "ufs_mem"); QEMU virt has none
UFS, UFS_SIZE = 0x04804000, 0x3000
TIMEBASE = 19200000
VTIMER_INTID = 19
# QEMU virt (-M virt,gic-version=3 -cpu cortex-a76): GICD/GICR and the generic timer's CNTFRQ
QEMU_GICD, QEMU_GICR, QEMU_TIMEBASE = 0x08000000, 0x080a0000, 62500000
QEMU_VTIMER_INTID = 27

BOOT_MANIFEST_HASH = hashlib.sha384(b'topaz boot manifest').digest()

# The iOS userland is the iPhone X (iPhone10,3 / D22AP) IPSW. MobileGestalt
# derives product type, device class, home-button-less UI, artwork etc. from
# the root identity and /product of the real D22AP device tree. That tree is
# an Apple artifact (kept out of git): extract it from the IPSW with
#   tools/remotezip.py URL DeviceTree.d22ap.im4p DIR
#   ipsw img4 im4p extract DIR/Firmware/all_flash/DeviceTree.d22ap.im4p -o d22ap.adt
D22AP_ADT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '../research/ipsw-x/d22ap.adt')


def d22ap_product(path=D22AP_ADT):
    """/product properties of the real D22AP tree, without the syscfg
    placeholders iBoot would fill in (flag bit set) and the node bookkeeping."""
    if not os.path.exists(path):
        print(f'warning: {path} missing, no /product node', file=sys.stderr)
        return None
    root, _ = adt.parse(open(path, 'rb').read())
    for c in root.children:
        if dict((k, v) for k, f, v in c.props).get('name') == b'product\0':
            return [(k, v) for k, f, v in c.props if not f and k not in ('name', 'AAPL,phandle')]
    raise SystemExit(f'{path}: no /product node')

u32 = lambda *v: struct.pack('<%dI' % len(v), *v)
u64 = lambda *v: struct.pack('<%dQ' % len(v), *v)
cstr = lambda s: s.encode() + b'\0'


def node(name, props=(), children=()):
    n = adt.Node()
    n.props = [('name', 0, cstr(name))] + [(k, 0, v) for k, v in props]
    n.children = list(children)
    return n


def build(seed=None, qemu=False, highmem=False, native=False, midmem=False):
    """qemu=True: same tree, but with QEMU virt's GIC addresses and CNTFRQ (tools/qemu-sweet.py)."""
    phys_base, mem_size = ((MID_PHYS_BASE, MID_MEM_SIZE) if midmem else
                           (HIGH_PHYS_BASE, HIGH_MEM_SIZE) if highmem else (PHYS_BASE, MEM_SIZE))
    gicd, gicr, timebase = (QEMU_GICD, QEMU_GICR, QEMU_TIMEBASE) if qemu else (GICD, GICR, TIMEBASE)
    seed = seed if seed is not None else os.urandom(64)
    phandle = iter(range(1, 1000))
    ph = lambda: ('AAPL,phandle', u32(next(phandle)))

    chosen = node('chosen', [
        ph(),
        ('dram-base', u64(phys_base)),
        ('dram-size', u64(mem_size)),
        ('random-seed', seed),
        ('debug-enabled', u32(1)),
        ('firmware-version', cstr('topaz-shim')),
        ('system-firmware-version', cstr('topaz-shim')),
        # iBoot's SHA-384 of the boot manifest; /sbin/mount -P 1 exits unless
        # it exists (bind mounts /private/preboot/<hex>/usr/standalone/firmware,
        # tolerating a missing directory). Fixed stand-in value.
        ('boot-manifest-hash', BOOT_MANIFEST_HASH),
        # libimg4 (cryptexd) aborts "failed to get crypto algorithm" without
        # it; real A11+ device trees say sha2-384.
        ('crypto-hash-method', cstr('sha2-384')),
        # MobileGestalt's screen class: display-scale (u32, default 0) comes
        # from /chosen, the pixel size from IOMobileFramebuffer. Scale 0 makes
        # SpringBoard's FBSDisplayMode "infxinf@0x" invalid. 1080x2400 @3x =
        # 360x800 points (like iPhone X class devices).
        ('display-scale', u32(3)),
        # XNU derives IOPlatformUUID (gethostuuid) from these on arm; without
        # them CFPreferences cannot use kCFPreferencesCurrentHost (no ByHost
        # files), so e.g. DataMigration never records LastSystemVersion and
        # migrates on every boot. Synthetic ECID (not the phone's); chip-id
        # of the A11 that the iPhone X userland expects.
        ('unique-chip-id', struct.pack('<Q', SWEET_ECID)),
        ('chip-id', u32(0x8015)),
        # Security status as on a dev-fused, undemoted board (tuple 0,0,1,1):
        # MobileGestalt reads these from /chosen, and mobileactivationd's
        # DeviceType then takes its native "Hactivation" branch (state
        # Activated without an activation record; no SEP, no baseband here).
        ('certificate-production-status', u32(0)),
        ('effective-production-status-ap', u32(0)),
        ('certificate-security-mode', u32(1)),
        ('effective-security-mode-sep', u32(1)),
    ], [node('memory-map', [ph()]),
        # Local boot policy (kern_csr.c): SIP relaxed like `csrutil disable`
        # (lp-sip0: untrusted kexts, unrestricted FS, task_for_pid, kernel
        # debugger, dtrace, nvram), lp-sip1: unauthenticated (unsealed) root.
        # The iOS shared cache is in no trust cache, so dyld can only map it
        # with CSR_ALLOW_UNRESTRICTED_FS (vm_unix.c shared region checks).
        node('asmb', [ph(), ('lp-sip0', u64(0x6f)), ('lp-sip1', u32(1))])])

    # TOPAZ SMP: only the four Cortex-A73 (gold) cores, MPIDR 0x100-0x103:
    # the Kryo 260 Silver cores have no 16K granule, so the shim boots xnu
    # on gold CPU 0x100 and parks the silver cluster. One cluster. The kernel defaults to one
    # and uses cpus=N to select an initial prefix (N <= 8).
    # QEMU retains its original single-CPU tree: its MPIDRs differ from sweet.
    cpu_nodes = []
    for core in range(1 if qemu else 4):
        cpu_nodes.append(node(f'cpu{core}', [
            ph(),
            ('device_type', cstr('cpu')),
            ('compatible', cstr('qcom,kryo260') + cstr('qcom,kryo470') + cstr('ARM,v8')),
            ('reg', u32(core if qemu else 0x100 | core)),  # actual MPIDR Aff1:Aff0, NOT logical ID
            ('cpu-id', u32(core)),   # descriptive: XNU assigns IDs by DT order
            ('cluster-id', u32(0)),  # descriptive; see ml_parse_cpu_topology
            ('cluster-type', u32(0)),  # homogeneous CLUSTER_TYPE_SMP, not AMP
            ('die-cluster-id', u32(0)),
            ('cluster-core-id', u32(core)),
            ('l2-cache-id', u32(0)),  # gold L2
            ('l3-cache-id', u32(0)),
            ('state', cstr('running' if core == 0 else 'stopped')),
            ('timebase-frequency', u32(timebase)),
            ('clock-frequency', u32(2803200000)),
            ('bus-frequency', u32(100000000)),
            ('memory-frequency', u32(1866000000)),
            ('peripheral-frequency', u32(timebase)),
            ('fixed-frequency', u32(timebase)),
        ]))
    cpus = node('cpus', [ph(), ('#address-cells', u32(1)),
                         ('#size-cells', u32(0))], cpu_nodes)

    gic = node('gic', [
        ph(),
        ('compatible', cstr('arm,gic-v3')),
        ('interrupt-controller', b''),
        ('virtual-timer-intid', u32(QEMU_VTIMER_INTID if qemu else VTIMER_INTID)),
        ('reg', u64(gicd - SOC_BASE, GICD_SIZE, gicr - SOC_BASE, GICR_SIZE)),
    ])
    arm_io = node('arm-io', [
        ph(),
        ('device_type', cstr('sweet-io')),
        ('compatible', cstr('sweet-io') + cstr('arm-io')),
        ('#address-cells', u32(2)),
        ('#size-cells', u32(2)),
        ('ranges', u64(0, SOC_BASE, 0x100000000 - SOC_BASE)),
    ], [gic,
        # No hardware: watchdogd checks IODeviceTree:/arm-io/wdt for no-pmu and
        # otherwise crashes trying to set the PMU boot stage (customer builds
        # ignore the -wdt_no_pmu boot-arg).
        node('wdt', [ph(), ('no-pmu', u32(1))]),
        # Main display: IOMobileFramebuffer.framework counts /arm-io children
        # with this device_type and reads the canvas size from the node
        # (the presented iPhone X size, see SweetFramebuffer.cpp);
        # the kernel's AppleCLCD matches the compatible string.
        # launchd starts wifid only when the device tree has a "wlan" node
        # (com.apple.wifid _LimitLoadToDeviceTree = wlan, marconi-wifi)
        node('wlan', [
            ph(),
            ('device_type', cstr('wlan')),
            ('compatible', cstr('topaz,wcn3988')),
        ]),
        node('disp0', [
            ph(),
            ('device_type', cstr('display-subsystem')),
            ('compatible', cstr('sweet,display')),
            # --native: present the real 1080x2400 panel (needed for direct MDP scanout,
            # which must match the canvas: sweetfb_native=1 with a 1125x2436 canvas makes
            # backboardd ask for a hardware scaler that does not exist)
            ('canvas-width', u32(1080 if native else 1125)),
            ('canvas-height', u32(2400 if native else 2436)),
        ])])

    fbcon = node('sweet-fbcon', [
        ph(),
        ('reg', u64(FB, FB_SIZE)),
        ('width', u32(FB_W)),
        ('height', u32(FB_H)),
        ('stride', u32(FB_STRIDE)),
    ])

    # bsd/dev/sweet_ufs.c: the UFS host ABL leaves running (absolute physical reg).
    ufs = node('sweet-ufs', [
        ph(),
        ('compatible', cstr('qcom,ufshc')),
        ('reg', u64(UFS, UFS_SIZE)),
    ])

    # pmap_compute_max_asids requires the node even when no overrides are set.
    # An absent pmap-max-asids property selects XNU's own MAX_ASIDS default.
    # No effaceable storage: keybagd then reads /private/var/keybags/systembag.kb
    # as a plain plist instead of decrypting it with the effaceable key.
    defaults = node('defaults', [ph(), ('no-effaceable-storage', u32(1))])

    # MobileGestalt HomeButtonType = IODeviceTree:/buttons home-button-type
    # (D22AP: 2, no Home button; absent = 0 = Home-button device).
    buttons = node('buttons', [
        ph(),
        ('button-names', cstr('volup') + cstr('voldown') + cstr('hold')),
        ('home-button-type', u32(2)),
        ('opposed-power-vol-buttons', u32(1)),
    ])

    product_props = d22ap_product()
    # Our framebuffer is sRGB 8-bit. With the iPhone X's 'P3' MobileGestalt says
    # HasExtendedColorDisplay and WebKit renders page tiles into 10-bit surfaces
    # ('w30r', 'b3a8') that this kernel does not provide: Safari pages stay blank.
    if product_props:
        product_props = [(k, cstr('sRGB') if k == 'artwork-display-gamut' else v) for k, v in product_props]
    product = [node('product', [ph()] + product_props)] if product_props else []

    # Identity of the iPhone X whose userland runs here: compatible[0] is
    # hw.model/hw.target (D22AP), model is hw.machine/hw.product
    # (iPhone10,3). 'xiaomi,sweet' and qcom,kryo470 stay because the
    # SweetPlatformExpert and CPU IONameMatch still use them.
    return node('device-tree', [
        ph(),
        ('compatible', cstr('D22AP') + cstr('iPhone10,3') + cstr('xiaomi,topaz') + cstr('xiaomi,sweet') + cstr('qcom,khaje') + cstr('AppleARM')),
        ('model', cstr('iPhone10,3')),
        ('target-type', cstr('D22')),
        ('target-sub-type', cstr('D22AP')),
        ('manufacturer', cstr('Apple Inc.')),
        ('#address-cells', u32(2)),
        ('#size-cells', u32(2)),
    ], [chosen, defaults, cpus, arm_io, fbcon, buttons] + product + ([] if qemu else [ufs]))


def a50_ram_range(nodes, fdt_reservations, bank_index=None):
    """Select one contiguous, unreserved FDT RAM range for XNU's ADT fields."""
    banks = memory_ranges(nodes)
    if not banks:
        raise ValueError('FDT contains no top-level memory ranges')
    for index, (_, address, size) in enumerate(banks):
        if address + size > 1 << 64:
            raise ValueError(f'FDT memory bank {index} overflows 64-bit physical addresses')
        for other_index, (_, other_address, other_size) in enumerate(banks[:index]):
            if address < other_address + other_size and other_address < address + size:
                raise ValueError(f'FDT memory banks {other_index} and {index} overlap')

    fixed, windows = reserved_memory_ranges(nodes)
    reserved = list(fdt_reservations)
    reserved.extend((address, size) for _, address, size in fixed + windows)
    chosen = nodes.get('/chosen', {})
    initrd_start_prop = chosen.get('linux,initrd-start')
    initrd_end_prop = chosen.get('linux,initrd-end')
    if initrd_start_prop is not None or initrd_end_prop is not None:
        starts = cells(initrd_start_prop)
        ends = cells(initrd_end_prop)
        if not starts or not ends:
            raise ValueError('FDT initrd start/end properties are incomplete')
        initrd_start = cell_value(starts)
        initrd_end = cell_value(ends)
        if initrd_end < initrd_start:
            raise ValueError('FDT initrd end precedes its start')
        reserved.append((initrd_start, initrd_end - initrd_start + 1))

    def free_parts(address, size):
        end = address + size
        clipped = sorted((max(address, start), min(end, start + length))
                         for start, length in reserved
                         if length > 0 and start < end and address < start + length)
        merged = []
        for start, stop in clipped:
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], stop))
            else:
                merged.append((start, stop))
        parts = []
        cursor = address
        for start, stop in merged:
            if start > cursor:
                part_start = (cursor + 0x3fff) & ~0x3fff
                part_end = start & ~0x3fff
                if part_end > part_start:
                    parts.append((part_start, part_end - part_start))
            cursor = max(cursor, stop)
        if cursor < end:
            part_start = (cursor + 0x3fff) & ~0x3fff
            part_end = end & ~0x3fff
            if part_end > part_start:
                parts.append((part_start, part_end - part_start))
        return parts

    if bank_index is None:
        eligible = []
        for index, (path, address, size) in enumerate(banks):
            parts = free_parts(address, size)
            if parts:
                largest = max(size for _, size in parts)
                eligible.append((index, path, largest))
        if len(eligible) != 1:
            choices = ', '.join(f'{index}:{path} (largest free 0x{size:x})'
                                for index, path, size in eligible) or 'none'
            raise ValueError(
                'FDT has multiple or no usable RAM banks; choose one with '
                f'--a50-memory-bank INDEX. Available: {choices}')
        bank_index = eligible[0][0]
    if bank_index < 0 or bank_index >= len(banks):
        raise ValueError(f'--a50-memory-bank {bank_index} is outside 0..{len(banks) - 1}')

    path, address, size = banks[bank_index]
    parts = free_parts(address, size)
    if not parts:
        raise ValueError(f'FDT memory bank {bank_index} ({path}) is fully reserved')
    parts.sort(key=lambda item: item[1], reverse=True)
    if len(parts) > 1 and parts[0][1] == parts[1][1]:
        raise ValueError(
            f'FDT memory bank {bank_index} has equally sized free ranges; '
            'the ADT format here can describe only one range')
    return parts[0]


def a50_cpus(nodes, timebase):
    """Build only the Cortex-A73 CPU cluster, whose cores support 16 KB pages."""
    cpus_node = nodes.get('/cpus')
    if cpus_node is None:
        raise ValueError('FDT has no /cpus node')
    address_cells = integer(cpus_node.get('#address-cells'), 1)
    if address_cells < 1 or address_cells > 2:
        raise ValueError(f'unsupported /cpus #address-cells: {address_cells}')

    psci = nodes.get('/psci', {})
    psci_compatible = string_list(psci.get('compatible'))
    psci_method = string_list(psci.get('method'))
    psci_available = (any(value in ('arm,psci', 'arm,psci-0.2')
                           for value in psci_compatible)
                      and psci_method in (['smc'], ['hvc']))

    candidates = []
    for path, props in sorted(nodes.items()):
        if not path.startswith('/cpus/cpu@') or path.count('/') != 2:
            continue
        compatible = string_list(props.get('compatible'))
        status = string_list(props.get('status'))
        if 'arm,cortex-a73' not in compatible or status not in ([], ['okay'], ['ok']):
            continue
        ids = cells(props.get('reg'))
        if len(ids) < address_cells:
            raise ValueError(f'{path} has an invalid MPIDR reg property')
        mpidr = cell_value(ids[:address_cells])
        enable = string_list(props.get('enable-method'))
        if enable != ['psci'] or not psci_available:
            raise ValueError(f'{path} does not have a verified PSCI CPU enable path')
        candidates.append((mpidr, path))

    candidates.sort()
    mpidrs = [mpidr for mpidr, _ in candidates]
    if len(mpidrs) != len(set(mpidrs)):
        raise ValueError('FDT contains duplicate Cortex-A73 MPIDRs')
    if not candidates or 0x100 not in mpidrs:
        raise ValueError('FDT does not expose the expected Cortex-A73 boot MPIDR 0x100')
    if any((mpidr >> 8) != 1 or (mpidr & 0xff) >= 8 for mpidr in mpidrs):
        raise ValueError('FDT Cortex-A73 MPIDR is outside the A50 cluster layout')

    cpu_nodes = []
    for logical_id, (mpidr, _path) in enumerate(candidates):
        core = mpidr & 0xff
        cpu_nodes.append(node(f'cpu{logical_id}', [
            ('AAPL,phandle', u32(logical_id + 1)),
            ('device_type', cstr('cpu')),
            ('compatible', cstr('qcom,kryo470') + cstr('arm,cortex-a73') + cstr('ARM,v8')),
            ('reg', u32(mpidr)),
            ('cpu-id', u32(logical_id)),
            ('cluster-id', u32(1)),
            ('cluster-type', u32(0)),
            ('die-cluster-id', u32(mpidr >> 8)),
            ('cluster-core-id', u32(core)),
            ('l2-cache-id', u32(1)),
            ('l3-cache-id', u32(0)),
            ('state', cstr('running' if logical_id == 0 else 'stopped')),
            ('timebase-frequency', u32(timebase)),
            ('peripheral-frequency', u32(timebase)),
            ('fixed-frequency', u32(timebase)),
        ]))
    return node('cpus', [
        ('AAPL,phandle', u32(100)),
        ('#address-cells', u32(1)),
        ('#size-cells', u32(0)),
    ], cpu_nodes)


def build_a50(nodes, fdt_reservations, memory_bank=None):
    """Build a minimal A50 ADT from values read from one stock raw FDT."""
    ram_base, ram_size = a50_ram_range(nodes, fdt_reservations, memory_bank)

    root_ac, root_sc = node_cells(nodes, '/', 2, 1)
    gics = [(path, props) for path, props in nodes.items()
            if props.get('interrupt-controller') is not None
            and any('gic' in value.lower()
                    for value in string_list(props.get('compatible')))]
    if len(gics) != 1:
        raise ValueError(f'expected one GIC interrupt-controller in FDT, found {len(gics)}')
    gic_path, gic_props = gics[0]
    gic_regs = reg_ranges(gic_props.get('reg'), root_ac, root_sc)
    if len(gic_regs) < 2 or any(size == 0 for _, size in gic_regs[:2]):
        raise ValueError(f'{gic_path} does not describe GICD and GICC register ranges')
    (gicd, gicd_size), (gicc, gicc_size) = gic_regs[:2]
    if any(value > 0xffffffff for base, size in ((gicd, gicd_size), (gicc, gicc_size))
           for value in (base, base + size - 1)):
        raise ValueError('A50 GIC register ranges do not fit the current 32-bit arm-io map')
    # arm-io uses a nonzero translation base; derive a 16 MiB-aligned window
    # containing the real GICD address read above.
    soc_base = gicd & ~0x00ffffff
    if soc_base == 0 or gicc < soc_base:
        raise ValueError('cannot derive a nonzero arm-io base from the FDT GIC ranges')

    timer = nodes.get('/timer', {})
    if not any('arm,armv8-timer' in value for value in string_list(timer.get('compatible'))):
        raise ValueError('FDT has no ARMv8 generic timer at /timer')
    timebase = integer(timer.get('clock-frequency'))
    if not timebase:
        raise ValueError('/timer has no single-cell clock-frequency')
    timer_irqs = cells(timer.get('interrupts'))
    gic_irq_cells = integer(gic_props.get('#interrupt-cells'), -1)
    if gic_irq_cells != 3 or len(timer_irqs) != 4 * gic_irq_cells:
        raise ValueError('/timer interrupts do not contain the four expected GICv2 tuples')
    virtual_irq = timer_irqs[2 * gic_irq_cells:3 * gic_irq_cells]
    if virtual_irq[0] != 1 or virtual_irq[1] > 15:
        raise ValueError('FDT virtual timer interrupt is not a GIC PPI')
    timer_intid = 16 + virtual_irq[1]
    if timer_intid != 27:
        raise ValueError(
            f'FDT virtual timer INTID is {timer_intid}, but the A50 XNU patch uses 27')

    cpu_nodes = a50_cpus(nodes, timebase)
    seed = os.urandom(64)
    phandle = iter(range(200, 1000))
    ph = lambda: ('AAPL,phandle', u32(next(phandle)))
    chosen = node('chosen', [
        ph(),
        ('dram-base', u64(ram_base)),
        ('dram-size', u64(ram_size)),
        ('random-seed', seed),
        ('debug-enabled', u32(1)),
        ('firmware-version', cstr('a50-shim')),
        ('system-firmware-version', cstr('a50-shim')),
        ('boot-manifest-hash', BOOT_MANIFEST_HASH),
        ('crypto-hash-method', cstr('sha2-384')),
        ('display-scale', u32(3)),
        ('unique-chip-id', struct.pack('<Q', SWEET_ECID)),
        ('chip-id', u32(0x8015)),
        ('certificate-production-status', u32(0)),
        ('effective-production-status-ap', u32(0)),
        ('certificate-security-mode', u32(1)),
        ('effective-security-mode-sep', u32(1)),
    ], [
        node('memory-map', [ph()]),
        node('asmb', [ph(), ('lp-sip0', u64(0x6f)), ('lp-sip1', u32(1))]),
    ])

    gic = node('gic', [
        ph(),
        ('compatible', cstr('arm,gic-v2') + gic_props['compatible']),
        ('interrupt-controller', b''),
        ('virtual-timer-intid', u32(timer_intid)),
        ('reg', u64(gicd - soc_base, gicd_size, gicc - soc_base, gicc_size)),
    ])
    arm_io = node('arm-io', [
        ph(),
        ('device_type', cstr('sweet-io')),
        ('compatible', cstr('sweet-io') + cstr('arm-io')),
        ('#address-cells', u32(2)),
        ('#size-cells', u32(2)),
        ('ranges', u64(0, soc_base, 0x100000000 - soc_base)),
    ], [gic, node('wdt', [ph(), ('no-pmu', u32(1))])])

    defaults = node('defaults', [ph(), ('no-effaceable-storage', u32(1))])
    buttons = node('buttons', [
        ph(),
        ('button-names', cstr('volup') + cstr('voldown') + cstr('hold')),
        ('home-button-type', u32(2)),
        ('opposed-power-vol-buttons', u32(1)),
    ])
    product_props = d22ap_product()
    if product_props:
        product_props = [(key, cstr('sRGB') if key == 'artwork-display-gamut' else value)
                         for key, value in product_props]
    product = [node('product', [ph()] + product_props)] if product_props else []
    return node('device-tree', [
        ph(),
        ('compatible', cstr('D22AP') + cstr('iPhone10,3') + cstr('xiaomi,sweet')
         + cstr('samsung,a50') + cstr('samsung,exynos9610') + cstr('AppleARM')),
        ('model', cstr('iPhone10,3')),
        ('target-type', cstr('D22')),
        ('target-sub-type', cstr('D22AP')),
        ('manufacturer', cstr('Apple Inc.')),
        ('#address-cells', u32(2)),
        ('#size-cells', u32(2)),
    ], [chosen, defaults, cpu_nodes, arm_io, buttons] + product)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--dump', action='store_true')
    parser.add_argument('--qemu', action='store_true')
    parser.add_argument('--highmem', action='store_true')
    parser.add_argument('--native', action='store_true')
    parser.add_argument('--midmem', action='store_true')
    parser.add_argument('--a50-fdt', type=Path,
                        help='build an A50 ADT from one raw stock FDT blob')
    parser.add_argument('--a50-memory-bank', type=int,
                        help='zero-based FDT memory-range index shown by a50-fdt-report.py')
    args = parser.parse_args()
    try:
        if args.a50_fdt:
            if args.qemu or args.highmem or args.native or args.midmem:
                parser.error('--a50-fdt cannot be combined with TOPAZ/QEMU memory flags')
            fdt_blob = args.a50_fdt.read_bytes()
            fdt_nodes = parse_fdt(fdt_blob)
            root = build_a50(fdt_nodes, memreserve_ranges(fdt_blob), args.a50_memory_bank)
        else:
            if args.a50_memory_bank is not None:
                parser.error('--a50-memory-bank requires --a50-fdt')
            root = build(qemu=args.qemu, highmem=args.highmem,
                         native=args.native, midmem=args.midmem)
    except (OSError, ValueError, struct.error) as exc:
        parser.error(str(exc))
    blob = adt.serialize(root)
    args.output.write_bytes(blob)
    if args.dump:
        adt.dump(adt.parse(blob)[0])
    print(f'{args.output}: {len(blob)} bytes', file=sys.stderr)
