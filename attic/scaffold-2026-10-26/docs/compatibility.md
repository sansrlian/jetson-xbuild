# Compatibility notes (important)

## ROS 2 ↔ JetPack ↔ Ubuntu

| Board JetPack | Board Ubuntu | Native ROS apt | Cross host image |
|---------------|--------------|----------------|------------------|
| 6.2.x         | 22.04        | Humble         | Not kilted/lyrical without source ROS in sysroot |
| 7.x           | 24.04        | Jazzy / source Kilted | **`kilted`** preferred |
| Future / custom | 26.04     | Lyrical        | **`lyrical`** |

Kilted (Ubuntu 24.04) and Lyrical (Ubuntu 26.04 Tier-1) **cannot** link cleanly
against a JetPack 6.2 glibc/libstdc++/CUDA userspace. Capture sysroots from
boards that already run the ROS distro you are cross-building.

## NVIDIA AI libraries

Always come from the **sysroot** (board), not from the amd64 host image:
CUDA toolkit, cuDNN, TensorRT, VPI, cuBLAS, NPP, multimedia API, optional
ONNX Runtime / DeepStream. The host image only provides cross GCC, QEMU,
gdb-multiarch, colcon, and optional Nsight report viewers.

## Ubuntu 26.04 base image

`Dockerfile.lyrical` uses `ubuntu:26.04`. If your registry lacks that tag yet,
build on a machine that can pull it, or temporarily retarget the FROM line once
Resolute images are mirrored.
