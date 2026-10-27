#!/usr/bin/env python3
"""Small, strict reader for one flattened device tree blob."""
from __future__ import annotations

import struct


FDT_MAGIC = 0xD00DFEED
FDT_BEGIN_NODE, FDT_END_NODE, FDT_PROP, FDT_NOP, FDT_END = 1, 2, 3, 4, 9


def parse_fdt(blob: bytes) -> dict[str, dict[str, bytes]]:
    if len(blob) < 40:
        raise ValueError("input is shorter than an FDT header")
    (magic, total, struct_off, strings_off, _rsv_off, _version, _last,
     _boot_cpu, strings_size, struct_size) = struct.unpack_from(">10I", blob)
    if magic != FDT_MAGIC or total < 40 or total > len(blob):
        raise ValueError("input is not one valid raw FDT blob")
    if (struct_off < 40 or strings_off < 40 or struct_off + struct_size > total
            or strings_off + strings_size > total):
        raise ValueError("FDT structure or strings block exceeds totalsize")

    strings_block = blob[strings_off:strings_off + strings_size]
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
            if path in nodes:
                raise ValueError(f"duplicate FDT node path {path}")
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
            if nameoff >= len(strings_block):
                raise ValueError("FDT property name offset exceeds strings block")
            end = strings_block.find(b"\0", nameoff)
            if end < 0 or pos + length > len(structure):
                raise ValueError("FDT property exceeds its block")
            prop_name = strings_block[nameoff:end].decode("ascii", "replace")
            if prop_name in nodes[stack[-1]]:
                raise ValueError(f"duplicate FDT property {stack[-1]}:{prop_name}")
            nodes[stack[-1]][prop_name] = structure[pos:pos + length]
            pos = (pos + length + 3) & ~3
        elif token == FDT_NOP:
            continue
        elif token == FDT_END:
            if stack:
                raise ValueError("FDT ended with unclosed nodes")
            return nodes
        else:
            raise ValueError(f"unknown FDT token {token} at structure offset {pos - 4}")
    raise ValueError("FDT structure has no end token")


def memreserve_ranges(blob: bytes) -> list[tuple[int, int]]:
    """Read the FDT header's address/size reservation table."""
    if len(blob) < 40:
        raise ValueError("input is shorter than an FDT header")
    magic, total = struct.unpack_from(">II", blob)
    reserve_off = struct.unpack_from(">I", blob, 16)[0]
    if magic != FDT_MAGIC or total < 40 or total > len(blob):
        raise ValueError("input is not one valid raw FDT blob")
    if reserve_off < 40 or reserve_off + 16 > total:
        raise ValueError("FDT reservation table offset is invalid")
    ranges = []
    pos = reserve_off
    while pos + 16 <= total:
        address, size = struct.unpack_from(">QQ", blob, pos)
        pos += 16
        if address == 0 and size == 0:
            return ranges
        if size == 0 or address + size > 1 << 64:
            raise ValueError("FDT contains an invalid memory reservation")
        ranges.append((address, size))
    raise ValueError("FDT reservation table has no terminator")


def cells(value: bytes | None) -> list[int]:
    if value is None or len(value) % 4:
        return []
    return list(struct.unpack(">" + "I" * (len(value) // 4), value))


def integer(value: bytes | None, default: int = 0) -> int:
    values = cells(value)
    return values[0] if len(values) == 1 else default


def string_list(value: bytes | None) -> list[str]:
    if value is None:
        return []
    raw = value.rstrip(b"\0")
    if not raw:
        return []
    return [part.decode("ascii", "replace") for part in raw.split(b"\0")]


def strings(value: bytes | None) -> str:
    return ",".join(string_list(value)) or "-"


def cell_value(values: list[int]) -> int:
    result = 0
    for cell in values:
        result = (result << 32) | cell
    return result


def reg_ranges(value: bytes | None, address_cells: int,
               size_cells: int) -> list[tuple[int, int]]:
    values = cells(value)
    width = address_cells + size_cells
    if (not values or address_cells < 1 or size_cells < 1
            or len(values) % width):
        return []
    result = []
    for offset in range(0, len(values), width):
        row = values[offset:offset + width]
        result.append((cell_value(row[:address_cells]),
                       cell_value(row[address_cells:])))
    return result


def node_cells(nodes: dict[str, dict[str, bytes]], parent: str,
               fallback_address: int = 2, fallback_size: int = 1) -> tuple[int, int]:
    props = nodes.get(parent, {})
    address = integer(props.get("#address-cells"), fallback_address)
    size = integer(props.get("#size-cells"), fallback_size)
    if address < 1 or address > 2 or size < 1 or size > 2:
        raise ValueError(f"unsupported cell widths under {parent}: {address}/{size}")
    return address, size


def memory_ranges(nodes: dict[str, dict[str, bytes]]) -> list[tuple[str, int, int]]:
    address_cells, size_cells = node_cells(nodes, "/", 2, 1)
    result = []
    for path, props in sorted(nodes.items()):
        if path.count("/") != 1 or string_list(props.get("device_type")) != ["memory"]:
            continue
        for address, size in reg_ranges(props.get("reg"), address_cells, size_cells):
            if size:
                result.append((path, address, size))
    return result


def reserved_memory_ranges(nodes: dict[str, dict[str, bytes]]) -> tuple[list[tuple[str, int, int]],
                                                                       list[tuple[str, int, int]]]:
    """Return fixed reservations and allocation windows for dynamic pools."""
    parent = "/reserved-memory"
    if parent not in nodes:
        return [], []
    address_cells, size_cells = node_cells(
        nodes, parent,
        integer(nodes.get("/", {}).get("#address-cells"), 2),
        integer(nodes.get("/", {}).get("#size-cells"), 1))
    fixed: list[tuple[str, int, int]] = []
    windows: list[tuple[str, int, int]] = []
    for path, props in sorted(nodes.items()):
        if not path.startswith(parent + "/") or path.count("/") != 2:
            continue
        if props.get("reg") is not None:
            ranges = reg_ranges(props.get("reg"), address_cells, size_cells)
            if not ranges:
                raise ValueError(f"cannot parse {path}/reg")
            fixed.extend((path, address, size) for address, size in ranges if size)
            continue
        if props.get("size") is None:
            continue
        if props.get("alloc-ranges") is None:
            # A dynamic carve-out with no bounds could land anywhere in RAM.
            windows.append((path, 0, 1 << 64))
            continue
        ranges = reg_ranges(props.get("alloc-ranges"), address_cells, size_cells)
        if not ranges:
            raise ValueError(f"cannot parse {path}/alloc-ranges")
        windows.extend((path, address, size) for address, size in ranges if size)
    return fixed, windows
