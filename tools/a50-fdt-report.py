#!/usr/bin/env python3
"""Report only A50 bring-up properties from a flattened device tree.

Usage: a50-fdt-report.py STOCK.dtb

The report deliberately excludes chosen/bootargs contents, which may carry
device identifiers. It accepts one raw FDT blob, not an Android DTB table.
"""
from __future__ import annotations

import argparse
import struct
from pathlib import Path


FDT_MAGIC = 0xD00DFEED
FDT_BEGIN_NODE, FDT_END_NODE, FDT_PROP, FDT_NOP, FDT_END = 1, 2, 3, 4, 9


def parse_fdt(blob: bytes) -> dict[str, dict[str, bytes]]:
    if len(blob) < 40:
        raise ValueError("input is shorter than an FDT header")
    (magic, total, struct_off, strings_off, _rsv_off, version, _last,
     _boot_cpu, strings_size, struct_size) = struct.unpack_from(">10I", blob)
    if magic != FDT_MAGIC or total < 40 or total > len(blob):
        raise ValueError("input is not one valid raw FDT blob")
    if struct_off + struct_size > total or strings_off + strings_size > total:
        raise ValueError("FDT structure or strings block exceeds totalsize")

    strings = blob[strings_off:strings_off + strings_size]
    structure = blob[struct_off:struct_off + struct_size]
    nodes: dict[str, dict[str, bytes]] = {}
    stack: list[str] = []
    pos = 0

    while pos + 4 <= len(structure):
        token = struct.unpack_from(">I", structure, pos)[0]
        pos += 4
        if token == FDT_BEGIN_NODE:
            end = structure.find(b"\0", pos)
            if end < 0:
                raise ValueError("unterminated FDT node name")
            name = structure[pos:end].decode("ascii", "replace")
            pos = (end + 4) & ~3
            path = (stack[-1].rstrip("/") + "/" + name) if stack else "/"
            nodes[path] = {}
            stack.append(path)
        elif token == FDT_END_NODE:
            if not stack:
                raise ValueError("unbalanced FDT end-node token")
            stack.pop()
        elif token == FDT_PROP:
            if not stack or pos + 8 > len(structure):
                raise ValueError("invalid FDT property token")
            length, nameoff = struct.unpack_from(">II", structure, pos)
            pos += 8
            if nameoff >= len(strings):
                raise ValueError("FDT property name offset exceeds strings block")
            end = nameoff
            while end < len(strings) and strings[end] != 0:
                end += 1
            if end == len(strings) or pos + length > len(structure):
                raise ValueError("FDT property exceeds its block")
            name = strings[nameoff:end].decode("ascii", "replace")
            nodes[stack[-1]][name] = structure[pos:pos + length]
            pos = (pos + length + 3) & ~3
        elif token == FDT_NOP:
            continue
        elif token == FDT_END:
            return nodes
        else:
            raise ValueError(f"unknown FDT token {token} at structure offset {pos - 4}")
    raise ValueError("FDT structure has no end token")


def cells(value: bytes | None) -> list[int]:
    if value is None or len(value) % 4:
        return []
    return list(struct.unpack(">" + "I" * (len(value) // 4), value))


def integer(value: bytes | None, default: int = 0) -> int:
    values = cells(value)
    return values[0] if len(values) == 1 else default


def strings(value: bytes | None) -> str:
    if value is None:
        return "-"
    return ",".join(part.decode("ascii", "replace")
                     for part in value.rstrip(b"\0").split(b"\0"))


def reg_ranges(value: bytes | None, address_cells: int,
               size_cells: int) -> list[tuple[int, int]]:
    values = cells(value)
    width = address_cells + size_cells
    if not values or width < 2 or len(values) % width:
        return []
    result = []
    for offset in range(0, len(values), width):
        row = values[offset:offset + width]
        address = 0
        size = 0
        for cell in row[:address_cells]:
            address = (address << 32) | cell
        for cell in row[address_cells:]:
            size = (size << 32) | cell
        result.append((address, size))
    return result


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

    total_memory = 0
    for path, props in sorted(nodes.items()):
        if path.count("/") != 1 or strings(props.get("device_type")) != "memory":
            continue
        regions = reg_ranges(props.get("reg"), address_cells, size_cells)
        total_memory += sum(size for _, size in regions)
        lines.append(f"MEMORY {path}: {show_reg(props.get('reg'), address_cells, size_cells)}")
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
