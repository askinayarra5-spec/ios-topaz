# Samsung Galaxy A50 port assessment

## Status

This repository does **not** boot on the Samsung Galaxy A50. Its current target is the Redmi Note 12 4G (`topaz`, Qualcomm SM6225). This file records the work needed to start a separate A50 board port; it does not claim that an A50 image has been built or tested.

The connected handset is now confirmed as `SM-A505F` (`a50` / `a50dd`), board `exynos9610`, Android 11 build `A505FDDS9CWA1`, CSC `TUR`. Samsung's [release notice](https://doc.samsungmobile.com/SM-A505F/006810190531/eng.html) lists that same build and Android 11. Samsung lists an Exynos 9610, four 2.3 GHz plus four 1.7 GHz CPU cores, and a 1080x2340 Super AMOLED display for the A50. Public SM-A505F device work identifies the Exynos 9610's Cortex-A73/Cortex-A53 clusters, Mali-G72 MP3 GPU and Samsung S-Boot; that project also records no fastboot boot path. Sources: [Samsung A50 specifications](https://www.samsung.com/fr/support/mobile-devices/quelles-sont-les-caracteristiques-techniques-du-galaxy-a50/) and [A50 device base](https://github.com/sadatdaniel/a50-halium).

## Why the existing image cannot be reused

| Existing topaz component | A50 work required |
| --- | --- |
| `shim/build.sh` packs a Xiaomi boot image v4 for the Xiaomi ABL flow. | Repack into the A50's Android boot-image layout using an A50 donor header, then verify the shim's S-Boot handoff. The A50 device base documents Odin/custom recovery rather than `fastboot boot`; the current shim image is not an A50 boot image. |
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

The work in this checkout is a hardware assessment and port checklist with an A50 Android boot-image repacker. There is no A50 ADT, shim, XNU patch set, built image or device boot result yet. The repository's existing topaz instructions remain valid only for the Redmi Note 12 4G. No A50 image should be flashed or booted from this tree.

## Live connected-device inspection (2026-10-27)

The first USB enumeration exposed only a Samsung `Gadget Serial` modem interface; after the phone was powered on and USB debugging authorized, Platform Tools 37.0.1 exposed the physical ADB device. Read-only properties confirm `SM-A505F`, Android 11 build `A505FDDS9CWA1`, CSC `TUR`, and an unlocked bootloader (`ro.boot.flash.locked=0`, `ro.boot.vbmeta.device_state=unlocked`). The ADB serial is deliberately omitted.

Live Android observations: 8 CPUs are online; Linux reports 5,740,828 kB total memory; the internal panel is 1080x2340 at 60 Hz; `getevent -pl` calls its touchscreen `sec_touchscreen`; `/sys/bus/i2c/devices/4-0048/driver` points to `mms_ts` (Melfas). `/proc/device-tree` lists `interrupt-controller@12300000`, `ufs@0x13520000`, and `decon_board_ea8076`. The shell can enumerate DT node names but is denied reads of DT property contents, so live `reg`, memory-reservation and boot-argument values are still unknown.

Read-only `/dev/block/by-name` symlink listing reports `boot=sda14`, `dtb=sda12`, `dtbo=sda13`, `recovery=sda15`, and `userdata=sda32`. No raw partition contents were read. `fastboot devices -l` is empty; no Download Mode transition has been attempted.

The public [A50 Halium device base](https://github.com/sadatdaniel/a50-halium) provides a useful A50 boot-image format reference: it reports a 57,671,680-byte boot partition and Samsung S-Boot, with no fastboot path. Its 2026-09-10 Ubuntu Touch release image was downloaded to the local cache and its SHA-256 matches the published `fe4e753a...` hash. The image uses Android boot header v1, 2048-byte pages, kernel load address `0x10008000`, and is 55,449,600 bytes. The repository describes its Ubuntu Touch port as untested on hardware, so this is a release artifact and format reference rather than a confirmed boot on this handset. `tools/pack_a50_boot.py` can repack this header format and enforces the reported partition limit; an unchanged repack was byte-for-byte identical to the donor. This does not verify that the topaz shim or XNU can boot through it. The device base also reports that S-Boot takes `bootargs` from the DT rather than the image header, so the topaz build's header `--cmdline` cannot be assumed to work.

No phone boot, flash, PIT read, or partition write has been performed. The host is missing the VC++ 2012 runtime needed to launch the cached Heimdall CLI, so no Heimdall command has reached the USB device. An A50 boot attempt remains blocked by the lack of a matching A50 image and verified Samsung boot path; the topaz Xiaomi boot image is incompatible. Next: obtain the matching `A505FDDS9CWA1` stock firmware, inspect its boot image and DTB/DTBO, then implement and validate the Samsung/Exynos bring-up. Preserve a stock restore path and confirm before any partition write.
