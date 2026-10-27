#!/usr/bin/env python3
"""Report only A50 bring-up properties from a flattened device tree.

Usage: a50-fdt-report.py STOCK.dtb

The report deliberately excludes chosen/bootargs contents, which may carry
device identifiers. It accepts one raw FDT blob, not an Android DTB table.
"""
from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from a50_fdt import cells, integer, memory_ranges, parse_fdt, reg_ranges, strings  # noqa: E402


def show_reg(value: bytes | None, address_cells: int,
             size_cells: int) -> str:
    ranges = reg_ranges(value, address_cells, size_cells)
    if not ranges:
        return "unavailable/unparsed"
    return ", ".join(f"0x{address:x}+0x{size:x}" for address, size in ranges)


def report(nodes: dict[str, dict[str, bytes]]) -> str:
    root = nodes.get("/", {})
    address_cells = integer(root.get("#address-cells"), 2)
    size_cells = integer(root.get("#size-cells"), 1)
    lines = [f"FDT root cells: address={address_cells}, size={size_cells}"]

    banks = memory_ranges(nodes)
    total_memory = sum(size for _, _, size in banks)
    for index, (path, address, size) in enumerate(banks):
        lines.append(f"MEMORY [{index}] {path}: 0x{address:x}+0x{size:x}")
    if total_memory:
        lines.append(f"MEMORY total described: 0x{total_memory:x} bytes")

    rm = nodes.get("/reserved-memory", {})
    rm_ac = integer(rm.get("#address-cells"), address_cells)
    rm_sc = integer(rm.get("#size-cells"), size_cells)
    for path, props in sorted(nodes.items()):
        name = path.rsplit("/", 1)[-1].lower()
        if (path.startswith("/reserved-memory/") and path.count("/") == 2
                and any(word in name for word in ("splash", "framebuffer", "fb"))):
            lines.append(f"RESERVED {path}: {show_reg(props.get('reg'), rm_ac, rm_sc)}")

    psci = nodes.get("/psci", {})
    if psci:
        lines.append(f"PSCI method: {strings(psci.get('method'))}; "
                     f"compatible: {strings(psci.get('compatible'))}")
    cpus = nodes.get("/cpus", {})
    cpu_ac = integer(cpus.get("#address-cells"), 1)
    for path, props in sorted(nodes.items()):
        if not path.startswith("/cpus/cpu@") or path.count("/") != 2:
            continue
        cpu_ids = cells(props.get("reg"))
        mpidr = 0
        for cell in cpu_ids[:cpu_ac]:
            mpidr = (mpidr << 32) | cell
        lines.append(f"CPU {path.rsplit('@', 1)[-1]}: MPIDR=0x{mpidr:x}; "
                     f"enable={strings(props.get('enable-method'))}; "
                     f"compatible={strings(props.get('compatible'))}")

    gic_path = next((path for path, props in sorted(nodes.items())
                     if "interrupt-controller" in path
                     and props.get("interrupt-controller") is not None), None)
    if gic_path:
        props = nodes[gic_path]
        lines.append(f"GIC {gic_path}: compatible={strings(props.get('compatible'))}; "
                     f"reg={show_reg(props.get('reg'), address_cells, size_cells)}")

    timer = nodes.get("/timer", {})
    if timer:
        ints = cells(timer.get("interrupts"))
        lines.append(f"TIMER compatible={strings(timer.get('compatible'))}; "
                     f"frequency={integer(timer.get('clock-frequency'))} Hz; "
                     f"interrupt cells=" + (" ".join(f"0x{x:x}" for x in ints) or "-"))

    for path, props in sorted(nodes.items()):
        name = path.rsplit("/", 1)[-1].lower()
        compat = strings(props.get("compatible"))
        if not ("framebuffer" in name or "splash" in name
                or "simple-framebuffer" in compat or "decon" in name):
            continue
        details = [f"compatible={compat}"]
        if props.get("reg") is not None:
            details.append(f"reg={show_reg(props['reg'], address_cells, size_cells)}")
        for prop in ("width", "height", "stride"):
            if props.get(prop) is not None:
                details.append(f"{prop}={integer(props[prop], -1)}")
        if props.get("format") is not None:
            details.append(f"format={strings(props['format'])}")
        lines.append(f"DISPLAY {path}: " + "; ".join(details))

    if not any(line.startswith(("MEMORY ", "GIC ", "TIMER ", "DISPLAY "))
               for line in lines[1:]):
        lines.append("No expected memory, GIC, timer, or display nodes were found.")
    lines.append("chosen/bootargs contents omitted.")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fdt", type=Path)
    args = parser.parse_args()
    try:
        print(report(parse_fdt(args.fdt.read_bytes())))
    except (OSError, ValueError, struct.error) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
