# Samsung Galaxy A50 port assessment

## Status

This repository does **not** boot on the Samsung Galaxy A50. Its current target is the Redmi Note 12 4G (`topaz`, Qualcomm SM6225). This file records the work needed to start a separate A50 board port; it does not claim that an A50 image has been built or tested.

The first likely target is the Exynos 9610 variant, commonly sold as SM-A505F/SM-A505FN. Confirm the exact model and firmware before using any device-specific data. Samsung lists an Exynos 9610, four 2.3 GHz plus four 1.7 GHz CPU cores, and a 1080x2340 Super AMOLED display for the A50. Public SM-A505F device work identifies the Exynos 9610's Cortex-A73/Cortex-A53 clusters, Mali-G72 MP3 GPU and Samsung S-Boot; that project also records no fastboot boot path. Sources: [Samsung A50 specifications](https://www.samsung.com/fr/support/mobile-devices/quelles-sont-les-caracteristiques-techniques-du-galaxy-a50/) and [A50 device base](https://github.com/sadatdaniel/a50-halium).

## Why the existing image cannot be reused

| Existing topaz component | A50 work required |
| --- | --- |
| `shim/build.sh` packs a Xiaomi boot image v4 for the Xiaomi ABL flow. | Establish the Samsung S-Boot handoff and a recoverable development boot path. The A50 device base documents Odin/custom recovery rather than `fastboot boot`; the current shim image is not an A50 boot image. |
| `tools/mkadt.py` hard-codes topaz RAM, GIC, timer, framebuffer, UFS controller, CPU IDs and Xiaomi/iPhone identity properties. | Collect the exact A50 variant's stock DT/DTB and boot arguments, then build an Apple Device Tree from measured A50 values. Do not substitute guessed MMIO addresses. |
| The base XNU series `0001–0079` came from the Redmi Note 10 Pro (`sweet`, Qualcomm SM7150); `0080+` adapts it to topaz (SM6225). The combined tree includes Qualcomm-specific CPU/GIC, framebuffer, UFS, QUP touch, PMIC, Wi-Fi/modem and Adreno paths. | Treat both patch groups as hardware-porting material. Reuse only the OS/userland compatibility pieces after reviewing them; replace the board, boot, interrupt, storage and display support for Exynos9610. The A50 has a Mali GPU, so the Adreno 610 path cannot be used. |
| `sweet_ufs.c` talks to the topaz Qualcomm UFS host at `0x4804000`, and the install guide writes the iOS image to topaz `userdata`. | Identify the A50 UFS controller and partition map from the exact stock firmware. Do not reuse the topaz block device, offsets, or `userdata` flash command. |
| Display setup assumes a 1080x2400 topaz panel and its Qualcomm MDP. | Add an Exynos display pipeline and the A50's 1080x2340 panel timing before expecting the iOS UI to appear. |

## Candidate hardware facts from Samsung's A50 kernel source

The public [Exynos 9610/A505F kernel source](https://github.com/samsung-galaxy-a50/kernel-source-9610/tree/master/arch/arm64/boot/dts/exynos) gives useful starting points. These are source-tree values, **not yet read from the connected handset**, and must be checked against its exact model, live DT and firmware before use.

| Area | Candidate from the source tree | Port implication |
| --- | --- | --- |
| CPUs | Cortex-A53 MPIDRs `0x0–0x3`; Cortex-A73 MPIDRs `0x100–0x103`; PSCI CPU enable | The A73 cluster IDs resemble topaz, but verify the running CPU, PSCI availability and 16K translation support on this handset. |
| Interrupts and timer | GICv2-compatible controller around `0x12301000–0x12306000`; ARMv8 generic timer at 26 MHz | The current XNU patches initialize Qualcomm GICv3. A50 needs an Exynos/GICv2 path and its timer rate. |
| Storage | Samsung Exynos UFS host at `0x13520000`, with controller-specific vendor and PHY ranges | Replace the Qualcomm UFS register protocol; confirm the installed partition table before adding a root-device path. |
| Display | Exynos DECON/DSIM; EA8076 panel, MIPI command mode, 1080x2340 at 60 Hz | The topaz MDP scanout and its 1080x2400 buffer are not reusable. |
| Touch | The A505 common tree describes Melfas MMS at I2C `0x48`; an SWA-open overlay describes Imagis IST40xx at `0x50` | Identify the handset's touch IC from its live DT or stock firmware instead of choosing one by model family. |

## Bring-up sequence

1. Confirm the phone's full model code (for example, `SM-A505F`) and stock firmware build. Record whether its bootloader is unlockable/unlocked. Keep the stock firmware and recovery available.
2. From the matching stock firmware, collect the kernel, DTB/DTBO, boot image metadata, command line and partition map. Record CPU MPIDRs and `ID_AA64MMFR0` granule support, RAM ranges/reservations, GIC/timer addresses, and the boot-time framebuffer. Separate facts read from the device from values inferred from another A50 variant.
3. Make an A50-only Apple Device Tree and a minimal shim handoff. First target CPU, timer, interrupt controller and logging; do not start the topaz display, touch, Wi-Fi, modem, GPU or battery drivers.
4. Validate a return-to-recovery path and a RAM-only boot route before adding persistent storage support. Samsung's boot flow differs from the `fastboot boot` command in this repository.
5. Add storage, display, touch, keys and power drivers one at a time, using the A50 stock kernel/device tree and hardware captures as references. Keep any iOS root image off the phone until the storage driver and partition mapping are independently verified.
6. Only after the board drivers work, adjust the presented device identity and resolution, then attempt the iPhone X iOS 16 userland.

## Current boundary

The work in this checkout is a hardware assessment and port checklist only. There is no A50 ADT, shim, XNU patch set, built image or device boot result yet. The repository's existing topaz instructions remain valid only for the Redmi Note 12 4G. No A50 image should be flashed or booted from this tree.
