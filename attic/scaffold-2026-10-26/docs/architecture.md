# Architecture

```
x86 workstation
  setup_jetson_docker.sh
        │
        ▼
  Docker (amd64)  jetson-orin-xbuild:{kilted|lyrical}
        │ mounts
        ├─ host ROS2 workspace  → /home/ubuntu/ros2_ws
        ├─ Jetson sysroot       → /opt/arm64_sysroot   (CUDA, TRT, cuDNN, VPI, ROS aarch64)
        ├─ toolchains overlay   → /home/ubuntu/toolchains
        └─ boards.yaml          → inventory of Orin Nano / NX / AGX
        │
        ├─ cross-colcon-build   (aarch64-linux-gnu + Orin cmake)
        ├─ arm64-chroot         (QEMU into sysroot for apt/rosdep)
        └─ board CLI            (deploy / gdb / nsys / ncu / ftrace) ──SSH──▶ Orin boards
```

## Distro pairing

| Host image | Host Ubuntu | Host ROS | Recommended JetPack sysroot |
|------------|-------------|----------|-----------------------------|
| kilted     | 24.04       | kilted   | JP 7.x (Ubuntu 24.04)       |
| lyrical    | 26.04       | lyrical  | Matching 26.04 board / source-built ROS |

JetPack 6.2 (Ubuntu 22.04) sysroots are ABI-incompatible with kilted/lyrical apt ROS.
Use Humble on JP6, or build the desired ROS from source into that sysroot.
