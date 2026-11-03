# Jetson Orin xbuild — agent notes

- Cross-build with `cross-colcon-build` (forces `toolchains/cross.cmake`).
- Select board SKU via `PRODUCT=ORIN_NANO|ORIN_NX|ORIN_AGX` (entrypoint copies matching cmake).
- Multi-board inventory: `/home/ubuntu/boards.yaml` — `board list|select|deploy|nsys|…`.
- Sysroot at `$ARM64_SYSROOT` must contain JetPack CUDA/TensorRT matching the boards.
- Host ROS (`$ROS_DISTRO`) is for tooling; target link libraries come from the sysroot.
- Prefer JetPack 7.x sysroots with `kilted`; `lyrical` needs matching board userspace or source ROS.
