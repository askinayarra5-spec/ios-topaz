#!/usr/bin/env python3
"""Repack an SM-A505F Android boot-image-v1 donor with a new kernel/ramdisk.

This only handles the verified A50 donor layout: 2048-byte pages, no second
stage image and no recovery-DTBO payload. It preserves the donor header and
its legacy ID field, as Samsung S-Boot ignores that ID on this device. It does
not make a kernel A50-compatible and it does not flash the output.

Usage: pack_a50_boot.py DONOR.img KERNEL|- RAMDISK|- OUT.img
Use '-' to keep that component from the donor.
"""
from __future__ import annotations

import argparse
import hashlib
import struct
from pathlib import Path


BOOT_PARTITION_BYTES = 57_671_680
ANDROID_MAGIC = b"ANDROID!"
V1_RECOVERY_DTBO_SIZE_OFFSET = 1632
V1_HEADER_SIZE_OFFSET = 1644


def align(value: int, page_size: int) -> int:
    return (value + page_size - 1) & ~(page_size - 1)


def read_components(data: bytes) -> tuple[int, bytes, bytes, bytes]:
    if len(data) < 2048 or data[:8] != ANDROID_MAGIC:
        raise ValueError("donor is not an Android boot image")

    kernel_size, _, ramdisk_size, _, second_size, _, _, page_size, version, _ = (
        struct.unpack_from("<10I", data, 8)
    )
    if version != 1:
        raise ValueError(f"expected Android boot header v1, found v{version}")
    if page_size != 2048:
        raise ValueError(f"expected 2048-byte pages, found {page_size}")
    if len(data) < V1_HEADER_SIZE_OFFSET + 4:
        raise ValueError("truncated v1 boot header")

    recovery_dtbo_size = struct.unpack_from(
        "<I", data, V1_RECOVERY_DTBO_SIZE_OFFSET
    )[0]
    header_size = struct.unpack_from("<I", data, V1_HEADER_SIZE_OFFSET)[0]
    if header_size < V1_HEADER_SIZE_OFFSET + 4 or header_size > page_size:
        raise ValueError(f"invalid v1 header_size {header_size}")
    if second_size or recovery_dtbo_size:
        raise ValueError(
            "donor has a second-stage or recovery-DTBO component; refusing "
            "to drop it"
        )

    kernel_off = page_size
    ramdisk_off = align(kernel_off + kernel_size, page_size)
    end = ramdisk_off + ramdisk_size
    if end > len(data):
        raise ValueError("donor is truncated before the end of its ramdisk")
    tail_off = align(end, page_size)
    if tail_off > len(data):
        raise ValueError("donor is truncated before the end of its final page")
    if any(data[kernel_off + kernel_size : ramdisk_off]):
        raise ValueError("donor has nonzero kernel alignment padding")
    if any(data[end:tail_off]):
        raise ValueError("donor has nonzero ramdisk alignment padding")
    if len(data) % page_size:
        raise ValueError("donor size is not page-aligned")
    tail = data[tail_off:]
    if any(tail):
        raise ValueError("donor contains unrecognized nonzero data after its ramdisk")
    return page_size, data[kernel_off : kernel_off + kernel_size], data[
        ramdisk_off : ramdisk_off + ramdisk_size
    ], tail


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("donor", type=Path)
    parser.add_argument("kernel", help="kernel image, or '-' to keep the donor")
    parser.add_argument("ramdisk", help="ramdisk image, or '-' to keep the donor")
    parser.add_argument("out", type=Path)
    args = parser.parse_args()

    donor = args.donor.read_bytes()
    page_size, donor_kernel, donor_ramdisk, donor_tail = read_components(donor)
    kernel = donor_kernel if args.kernel == "-" else Path(args.kernel).read_bytes()
    ramdisk = donor_ramdisk if args.ramdisk == "-" else Path(args.ramdisk).read_bytes()

    header = bytearray(donor[:page_size])
    struct.pack_into("<I", header, 8, len(kernel))
    struct.pack_into("<I", header, 16, len(ramdisk))

    image = bytes(header) + kernel + b"\0" * (-len(kernel) % page_size)
    image += ramdisk + b"\0" * (-len(ramdisk) % page_size) + donor_tail
    if len(image) > BOOT_PARTITION_BYTES:
        raise SystemExit(
            f"image is {len(image)} bytes; A50 boot partition limit is "
            f"{BOOT_PARTITION_BYTES} bytes"
        )

    args.out.write_bytes(image)
    check = args.out.read_bytes()
    _, check_kernel, check_ramdisk, check_tail = read_components(check)
    if check_kernel != kernel or check_ramdisk != ramdisk or check_tail != donor_tail:
        raise SystemExit("output verification failed")

    print(f"kernel  {len(kernel):>10} bytes  sha256 {hashlib.sha256(kernel).hexdigest()}")
    print(f"ramdisk {len(ramdisk):>10} bytes  sha256 {hashlib.sha256(ramdisk).hexdigest()}")
    print(f"output  {len(image):>10} bytes  sha256 {hashlib.sha256(image).hexdigest()}")
    print(f"limit   {BOOT_PARTITION_BYTES:>10} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
