# jxb — Jetson Orin cross-dev environment

Build ROS 2, CUDA, TensorRT and ONNX Runtime code for NVIDIA Jetson Orin on a strong x86_64 machine, then deploy,
run and debug it on one or many boards. Inspired by the Renesas RZ/V2H RDK `xbuild` container, rebuilt for the
NVIDIA world. Roadmap and decisions: [PLAN.md](PLAN.md).

What is different from the Renesas-style setup:

- **The sysroot is built from the board's own apt repositories, not mirrored from a board.** apt resolves the
  arm64 package closure (Ubuntu ports, the L4T r39.2 `common`/`som`/`ffmpeg` repos with NVIDIA's pinning, ROS 2);
  the packages are only extracted, so no QEMU and no board are needed. Rebuilding from the cache takes ~1 min.
- **CUDA is compiled by the x86_64 `nvcc`** of exactly the board's version (13.2.86), with the board's
  `targets/sbsa-linux` headers and libraries from the sysroot. No CUDA builds on the Nano anymore.
- **Source-built extras for the board:** ONNX Runtime with CUDA + TensorRT EP for sm_87 (the released aarch64
  CUDA EP can't run on Orin) and acados; picked up by the sysroot automatically once built.
- **Profiles** (`profiles/*.toml`) pin everything that must match: JetPack/L4T, CUDA, ROS distro. Switching distros
  = `--profile`. Build/install trees are per profile (`build-<profile>/`, `install-<profile>/`).
- **Workspaces are mounted at their host path**, so `compile_commands.json`, debug info and clangd work on the host
  and in the container alike.

## Profiles

| Profile | Board | ROS 2 on the board | Status |
|---|---|---|---|
| `jp721-jazzy` | JetPack 7.2.1 (L4T 39.2.1, Ubuntu 24.04, CUDA 13.2, TensorRT 10.16) | Jazzy (native) | tests pass; SAM: all but 2 packages (they need Kilted APIs) |
| `jp721-kilted` | same | Kilted (native, Tier-1 on 24.04) | tests pass; SAM: all packages except `pylon_camera_package` (Basler SDK) |
| `jp721-lyrical-isaac` | same | Lyrical native (NVIDIA Isaac ROS Buildfarm for 24.04) + Isaac ROS 5.0 | tests pass (incl. Isaac ROS); SAM: all but `oak_camera_package` (no depthai), `localization_package` (tf2 `.hpp`), Pylon |
| `jp721-lyrical` | same | Lyrical in an Ubuntu 26.04 container (GPU libs from the board) | tests pass; SAM: all but `localization_package` (needs Lyrical tf2 `.hpp`) and Pylon; board-side container not built yet |

## Quick start

```bash
./jxb image build                      # xbuild image for the profile (once per profile)
./jxb sysroot build                    # sysroot from the board's apt repos (~8 GB download once, shared)
./jxb up -w ~/path/to/ros2_ws          # long-lived dev container, workspace mounted at the same path
./jxb test                             # cross-build + check the jxb test suite (CUDA, TensorRT, ORT, ONNX, ROS)

cd ~/path/to/ros2_ws
../jetson-orin-xbuild/jxb build --base-paths src --packages-up-to object_detection_package
```

Pick the profile with `--profile NAME` or `profile = "..."` in `jxb.toml` (local, gitignored):

```toml
profile = "jp721-kilted"
workspaces = ["/home/me/ros2_ws"]

[sysroot]
extra_sets = ["ws-{profile}-ros2"]     # rosdeps of the workspace, from: jxb sysroot rosdeps -w <ws>
```

## Boards

Copy `boards.example.toml` to `boards.toml`. jxb uses SSH keys only; set them up once per board:

```bash
ssh-copy-id nvidia@192.168.178.83
```

```bash
./jxb board check nano-lab             # JetPack/CUDA/TensorRT/ROS versions on the board vs. the sysroot lock
./jxb test --board nano-lab            # run the cross-built tests on the board (only writes ~/jxb/<profile>)
./jxb deploy nano-lab --ws ~/ros2_ws   # rsync install-<profile>/ to the board
./jxb run nano-lab -- ros2 run object_detection_package object_detection
```

## Layout

| Path | What |
|---|---|
| `jxb` | host CLI (Python, stdlib only) |
| `profiles/` | board/distro profiles (`inherit` supported) |
| `docker/xbuild.Dockerfile` | x86_64 image: aarch64 GCC 13, nvcc, host ROS 2 (generators), qemu-user |
| `sysroot/build_sysroot.py` | sysroot builder (apt resolve + extract + postinst emulation + fixups) |
| `sysroot/sets/` | package sets (`{ros_distro}` placeholder); `ws-*` sets come from `jxb sysroot rosdeps` |
| `sysroot/fixups.toml` | patches for sysroot files that only break when building on the host |
| `toolchain/orin.toolchain.cmake` | CMake toolchain (C/C++/CUDA, Python, pkg-config, rpath-link) |
| `toolchain/bin/` | in-container tools: `cross-colcon-build`, `jxb-elfcheck`, `jxb-rosdeps` |
| `extras/` | source builds jxb packs as sysroot/board extras: `onnxruntime/` (CUDA + TensorRT EP, sm_87), `acados/` |
| `tests/` | cross-build test suite and the board runner |
| `docs/` | [findings and pitfalls](docs/findings.md) |
| `attic/` | the earlier, untested scaffold (2026-10-26), kept for reference |
