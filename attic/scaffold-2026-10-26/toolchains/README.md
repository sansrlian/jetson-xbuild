# jetson-orin-xbuild toolchains

Fork-style replacement for Renesas `ubuntu_xbuild_toolchains`, targeting NVIDIA
Jetson Orin Nano / NX / AGX with multi-board deploy and Nsight workflows.

| File | Purpose |
|------|---------|
| `orin_*_cross.cmake` | Per-SKU CMake toolchain (`PRODUCT`) |
| `cross-colcon-build.sh` | colcon wrapper with forced toolchain |
| `arm64-chroot.sh` | QEMU chroot into Jetson sysroot |
| `scripts/board` | Multi-board deploy/debug/trace CLI |
| `.vscode/*` | Deploy, GDB, Nsight tasks |
