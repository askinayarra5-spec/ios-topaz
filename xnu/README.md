# XNU for SWEET and the experimental A50 target

Built on GitHub's macOS arm64 runners by `.github/workflows/xnu.yml`, using
[blacktop/darwin-xnu-build](https://github.com/blacktop/darwin-xnu-build) (MIT) at a pinned commit.

- `MACOS_VERSION=13.5` -> xnu-8796 (same generation as iOS 16.6/16.7, the last iOS for iPhone 8).
- `patches/*.patch` are copied into the build script's patch dir and applied to the xnu tree with `git apply`.
- `MACHINE_CONFIG=SWEET` builds the existing SWEET target. `MACHINE_CONFIG=A50` is an experimental Samsung Galaxy A50 / Exynos 9610 kernel configuration, currently limited to an initial GICv2 path; it is not a bootable handset image.
- Output: the selected machine configuration's kernel as a workflow artifact.

Fetch the latest build: `gh run download -R dipa-studio/ios-sweet -n xnu-kernel -D xnu/out`
