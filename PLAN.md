# Plan: Orin cross-dev / deploy / trace ecosystem and JetsonPlanner

Created 2026-11-02. Living document: tick boxes and add dates as work progresses.
Supersedes the "Open TODOs" in `HANDOFF.md` (that file stays as history).

**Goal:** a DevOps ecosystem for a fleet of Jetson Orins (Nano first, AGX too), inspired by the Renesas RZ/V2H RDK
`xbuild` environment:

1. **xbuild (product 1):** a Docker image that mounts the ROS 2 workspace and, on a strong x86 machine,
   cross-compiles and links everything for the Orin, including CUDA/TensorRT code, without mirroring from boards.
   It then deploys, debugs and traces on many Orins at once and can switch between ROS 2 distros
   (Kilted, Lyrical, ...).
2. **Golden image:** the boards stay on **stock JetPack** (no custom Ubuntu rootfs). The pipeline produces one
   provisioning bundle on top of it (our apt repo with cross-built `.deb`s, container images, agent, tools, config),
   and optionally a cloned flash image of a provisioned board for massflash. Every Orin runs exactly the same
   JetPack + bundle.
3. **JetsonPlanner (product 2, later):** a native GUI app (no .NET), inspired by ArduPilot Mission Planner.
   It flashes Orins, shows live telemetry, and analyzes traces and logs for many boards. It is later merged
   with xbuild into one system.

---

## 1. Current state (verified 2026-11-02)

| Item | State |
|---|---|
| This repo | Scaffold from a Cursor session (2026-10-26): launcher, Dockerfiles `kilted`/`lyrical`, Orin CMake toolchains, `board` CLI, VS Code tasks, docs. **Nothing built or tested; no commits.** |
| Renesas reference | `/home/samuela/Projekte/setup_rdk_docker.sh`; image `ghcr.io/renesas-rdk/rzv2h_ubuntu_xbuild:multiarch` (14.5 GB, Ubuntu 24.04, ROS Jazzy, sysroot `/opt/arm64_sysroot` 4.4 GB baked in); extracted evidence in `/home/samuela/Projekte/rea/.cache/rdk-re/`; toolchain repo is open source (`renesas-rdk/ubuntu_xbuild_toolchains`) |
| SAM dev image | `sam-dev.cs.hm.edu:5023/sam-dev/ros2:latest`: **amd64, Ubuntu 26.04, ROS 2 Lyrical (apt), CUDA 13.3**, ONNX Runtime, CV-CUDA, Pylon, acados, depthai, mavros, foxglove. A desktop dev image, not an Orin image |
| Sysroots | none captured yet (`sysroots/` empty) |
| Orins | **one Orin Nano with JetPack 7.2.1** (stays on stock JetPack). JP 7.2.1 = Jetson Linux 39.2.1, **Ubuntu 24.04**, kernel 6.8, CUDA 13.2.x, cuDNN 9.20, TensorRT 10.16.2, VPI 4.1.3, DeepStream 9.1; Orin Nano flashes in Super Mode by default (NVIDIA download page; exact CUDA patch level to confirm on the board) |
| Workstation | 20 cores, 15 GB RAM, 828 GB free disk. RAM is tight for large parallel C++/CUDA builds |

### Problems with today's workflow
- **P1 — board mirroring:** sysroots are rsync'd from live Nanos. That is not reproducible, drifts per board,
  and needs a prepared board before anything can be built.
- **P2 — CUDA under emulation:** building CUDA/TensorRT code inside the QEMU arm64 chroot is slow, and nvcc or
  linking often fails. Today the workaround is building on the Nano and linking in xbuild.
- **P3 — distro/ABI mismatch:** the SAM stack is Lyrical on Ubuntu 26.04 with CUDA 13.3. The board is Ubuntu
  24.04 with CUDA 13.2. Lyrical apt binaries target 26.04, so **Lyrical cannot run natively from apt on stock
  JetPack 7.2.1.** Natively on 24.04 are Jazzy (also Isaac ROS's distro) and Kilted. See D2/D3.
- **P5 — CUDA minor version:** code must be built against the board's CUDA 13.2 (not 13.3 from the SAM image),
  and TensorRT engines must be built with TensorRT 10.16 on the board.
- **P4 — no fleet view:** no inventory-wide deploy, health, trace collection or flashing.

---

## 2. Target architecture

```
x86 build host(s)                                           Orin fleet (Nano / AGX)
┌──────────────────────────────────────────────┐            ┌─────────────────────────┐
│ xbuild image (per ROS distro)                │            │ stock JetPack 7.2.1     │
│  ├─ cross GCC aarch64 + nvcc 13.2 (x86 host, │  deploy    │  + provisioning bundle: │
│  │   aarch64 target: cuda-cross-aarch64)     │──rsync────▶│  our .debs, containers  │
│  ├─ sysroot = built from apt, not mirrored   │  OTA/flash │  CUDA/TRT/cuDNN/VPI     │
│  │   (apt resolve+extract: Ubuntu ports, L4T │            │  ROS 2 + SAM stack      │
│  │    repo + ROS repo) → versioned artifact  │            │  orin-agent (daemon)    │
│  ├─ QEMU only for apt/postinst/rosdep        │◀─traces────│  nsys/ncu/perf/LTTng/   │
│  ├─ cross-colcon-build, ccache/sccache       │  telemetry │  trace-cmd/tegrastats   │
│  ├─ fleet CLI (board …) ─────────────────────┼──gRPC/SSH─▶│  gdbserver              │
│  └─ bundle builder (apt repo + images)       │            └─────────────────────────┘
└──────────────────────────────────────────────┘
                 ▲ same agent API
        JetsonPlanner (native GUI): flash, telemetry, trace analytics, multi-board
```

Design rules:
- **The sysroot and the board come from the same package lock.** One manifest
  (`{L4T release, Ubuntu release, ROS distro, package versions}`) produces the sysroot (for linking) and pins the
  versions the provisioning bundle installs on stock JetPack. This removes ABI drift (P1, P3).
- **No compiling under emulation.** CUDA code uses x86 nvcc with the aarch64 cross target. QEMU only runs package
  maintainer scripts. A native arm64 builder (cloud or Orin AGX as a build node) is a fallback only.
- **One target-side agent (`orin-agent`)** with a stable API (health, telemetry, deploy, run, trace start/stop/
  fetch, flash state). The xbuild CLI and JetsonPlanner are both clients. That makes the later merge cheap.
- **Every combination is a profile:** `profiles/<name>.yaml` = board class × L4T × Ubuntu × ROS distro. Switching
  distros = switching profile (host image + sysroot + image recipe).

---

## 3. Workstreams

### W1. Reference analysis (Renesas + others)
Renesas: mostly done (see HANDOFF). Still open: start the image, run one real cross-build in it, and record
what the RDK does well (one-command setup, baked sysroot, VS Code integration) and badly (sshpass, 14.5 GB
image, single board).

Other environments to study, **from public docs, open-source code and observable behaviour**:

| Reference | Why | Source |
|---|---|---|
| NVIDIA SDK Manager, JetPack cross-compile container, `l4t_initrd_flash.sh` / massflash, NVIDIA OTA | official flash/OTA/cross path we must interoperate with | Linux_for_Tegra BSP scripts (readable shell/Python), docs |
| OE4T `meta-tegra` + `meta-ros` (Yocto) | reproducible Orin images from source; how they package CUDA/L4T | open source |
| Isaac ROS dev containers (`run_dev.sh`), Isaac ROS buildfarm | NVIDIA's own ROS/CUDA container layout | open source |
| NXP i.MX (Yocto SDK), TI Processor SDK, Toradex Torizon, Foundries.io, Mender/RAUC/SWUpdate, balena | cross SDK, OTA, fleet-management patterns | docs, mostly open source |
| QGroundControl (Qt/C++), Mission Planner (C#) | GUI concepts for JetsonPlanner | open source (Apache-2.0/GPLv3; GPL-3.0) |

Scope: the whole product is for **private use only**. Commercial tools we own or can use legitimately may also
be reverse engineered (REA + Ghidra are set up) to understand and re-implement the features we need, e.g.
protocols, file formats, flash/OTA flows and trace formats. No circumvention of licence checks or copy protection.
Because nothing is distributed, GPL code (Mission Planner) and NVIDIA binaries may be used freely internally.
Revisit this if the product is ever shared.

### W2. Reproducible sysroot (solves P1)
- Build the sysroot with a private arm64 apt (resolve + extract, no emulation; `sysroot/build_sysroot.py`) from
  Ubuntu 24.04 ports + the NVIDIA L4T r39.2 apt repo
  (`repo.download.nvidia.com/jetson/...`, the same repo stock JetPack uses) + ROS apt repo + Isaac ROS apt repo,
  from a locked package list. Only `-dev` packages and their libraries; no live board needed. Because the board
  installs from the same repos, sysroot and stock JetPack match.
- Version it as an artifact (tarball or OCI layer, e.g. `sysroot:jp<ver>-<ubuntu>-<distro>-<date>`), so the xbuild
  image pulls it like the RDK bakes `/opt/arm64_sysroot`.
- Keep `jetson_sysroot_sync.sh` only as a diff tool: compare a live board against the locked sysroot to detect
  drift.

### W3. CUDA/TensorRT cross-compilation (solves P2)
- Install the CUDA **13.2** cross packages for the Jetson/L4T target (Orin `sm_87` stays on the L4T path, unlike
  Thor, which is SBSA; confirm the exact package name for JP 7.2) on the x86 image. nvcc runs natively on x86 and
  emits `sm_87` code. Link against the sysroot's `libcudart`, TensorRT, cuDNN.
- Extend `orin_*_cross.cmake`: `CMAKE_CUDA_COMPILER`, `CMAKE_CUDA_ARCHITECTURES=87`, `CUDAToolkit_ROOT`,
  `-ccbin aarch64-linux-gnu-g++`, TensorRT/cuDNN find hints.
- Proof packages: a CUDA kernel package, a TensorRT engine runner, and the real `object_detection_package`.
  Success = binaries built in xbuild run unchanged on the Nano.
- Build speed: ccache/sccache, `--parallel-workers` tuned to the 15 GB RAM, optional remote build host.

### W4. Golden image = stock JetPack + provisioning bundle
- Base: stock JetPack 7.2.1, flashed unchanged (`l4t_initrd_flash.sh` / SDK Manager flow). No custom rootfs.
- Bundle, produced by the xbuild pipeline and versioned together with the sysroot manifest:
  - our apt repo with cross-built `.deb`s (ROS packages, SAM nodes, `orin-agent`), installed natively;
  - container images (`nvidia-container-toolkit`, `--runtime nvidia`) for anything needing another distro (D2);
  - tracing tools, `nvpmodel`/clock profiles, systemd units, SSH keys, `boards` config.
- Apply with one idempotent `provision` command over the agent/SSH (one board or the whole fleet).
- Optional: clone a provisioned board (`l4t_backup_restore` / initrd-flash backup) into a massflash image.
- Updates: new bundle version = apt upgrade + image pull, no reflash.

### W5. Fleet deploy / debug
- `orin-agent` (small C++ daemon, shares protobuf/gRPC definitions with JetsonPlanner) with gRPC over mTLS;
  SSH keys only, no sshpass. Cross-built and shipped as a `.deb` by xbuild itself.
- `board` CLI talks to the agent: `list`, `deploy [--all|--group]`, `run`, `logs`, `gdbserver` (unique port per
  board), `reboot`, `flash-status`, `exec`.
- Per-board `ROS_DOMAIN_ID`, parallel deploy with a per-board result table.
- VS Code: launch configs generated per board (gdb-multiarch, sysroot-aware `solib-search-path`).

### W6. Tracing and analytics
- Target: `nsys` CLI, `ncu`, `perf`, `trace-cmd`/ftrace, LTTng + `ros2_tracing`, `tegrastats`/jtop, `bpftrace`
  (kernel config permitting).
- One command: start a synchronized trace on N boards, collect into `traces/<session>/<board>/`, with board and
  image metadata.
- Analysis: Perfetto (trace processor + UI) as the common format, Nsight GUI on the host for `.nsys-rep`;
  scripted reports (latency per ROS callback, GPU utilisation, RAM per process). This also gives the on-device
  numbers the inference plan in `quantization-aware-model/PLAN.md` needs.

### W7. JetsonPlanner (after W1–W6 have a stable agent API)
- Native GUI in **C++** (decided), no .NET. Framework: **Qt 6 / QML** (like QGroundControl: charts, serial/USB,
  maps, mature tooling). QGroundControl and Mission Planner serve as references.
- Features: board discovery and inventory, flashing (wraps W4 incl. recovery-mode USB), live telemetry dashboard
  (tegrastats, ROS topics, temperatures, power), log and trace browser (W6), deploy buttons (W5), multi-board view.
- Integration: JetsonPlanner uses the same agent API and profile/manifest files. Later the xbuild container becomes
  its build backend.
- Bonus: embeds the RDP view from W8 per board.

### W8. Remote GUI besides SSH (bonus, after the core works)
Purpose: debugging GUI tools that must run on the board. Constraint: the **Orin Nano has no hardware video
encoder (NVENC)**, so pixel streaming costs CPU, and a desktop session costs several hundred MB of RAM.

| Priority | Use | Tool |
|---|---|---|
| 1. Single remote windows | Debugging tools on the board (Nsight, rqt, GUI test apps) | Waypipe (Wayland) or X11 forwarding over SSH; check which session JetPack 7.2.1 runs |
| 2. Full desktop, on demand | Rare cases needing the whole desktop | RDP (GNOME Remote Desktop / xrdp) over an SSH tunnel, client Remmina/FreeRDP; fallback VNC |

- Not needed: a remote data/visualization layer (Foxglove etc.).
- `board gui <name> -- <app>` opens a single window; `board gui <name> --desktop` starts RDP on demand.
- Boards boot to `multi-user.target`; the desktop runs only when requested.
- JetsonPlanner may later embed the RDP view (FreeRDP library).

---

## 4. Phases and to-dos

### Phase 0: Decisions and inventory (½ day)
- [x] Board OS: stock JetPack, no custom rootfs (2026-11-02)
- [x] JetsonPlanner language: C++ (2026-11-02)
- [x] Inventory: one Orin Nano on JetPack 7.2.1 (2026-11-02)
- [x] Nano details from the old setup (2026-11-02): Orin Nano Super 8 GB, JetPack 7.2.1, ROS 2 Jazzy native,
      `nvidia@192.168.178.83`, workspace `/home/nvidia/ros2_ws` (in `boards.toml`, gitignored)
- [x] SSH key login to the Nano (done by the user) and `jxb board check nano-lab` (2026-11-03, findings F35)
- [x] D2: Lyrical on the board via container first, then source build; both must stay supported (2026-11-02)
- [x] Remote GUI besides SSH is a bonus (W8) (2026-11-02)
- [x] Baseline committed, remote repo created: `sansrlian/jetson-xbuild` on GitHub (2026-11-03/04)

### Phase 1: Minimal working xbuild (Jazzy/Kilted on 24.04, one Nano)
Implementation notes and every pitfall found on the way: [docs/findings.md](docs/findings.md).
- [x] New implementation replaces the scaffold (moved to `attic/`): `jxb` CLI, profiles, image, sysroot builder,
      toolchain, test suite (2026-11-02)
- [x] xbuild image `jxb:<profile>`: Ubuntu 24.04, aarch64 GCC 13, x86 nvcc 13.2.86 (= board), host ROS 2 (2026-11-02)
- [x] W2: sysroot from the board's apt repos without emulation (apt resolve + extract + postinst emulation):
      ~950 packages, 16 GB, 8 GB download once, ~1 min rebuild from cache (2026-11-02)
- [x] W3: CUDA 13.2 cross toolchain + CMake (sm_87, RDC, cuBLAS, NVTX), TensorRT 10.16, ONNX Runtime 1.31 (CPU),
      ONNX 1.17 from source, CV-CUDA 0.16, ROS 2 interfaces + CUDA node: 12/12 tests incl. qemu runs (2026-11-02)
- [x] Profiles `jp721-jazzy` and `jp721-kilted` (inheritance); SAM workspace: 24/24 buildable packages with
      Kilted, 22/24 with Jazzy (SAM uses Kilted APIs) (2026-11-02)
- [x] ONNX Runtime 1.31 with CUDA + TensorRT EP for sm_87 from source (`extras/onnxruntime`, lean flavor), in all
      sysroots; needed a CUDA 13.2 CCCL fixup (findings F22) (2026-11-02)
- [x] acados 0.5.2 cross build + host-side code generation; `acados_model_package` and `trajectory_planner_package`
      cross-build (2026-11-02)
- [ ] Pylon SDK (Basler) as extra for `pylon_camera_package`
- [x] `jxb-elfcheck` also checks GLIBC_/GLIBCXX_ symbol versions against the sysroot (2026-11-02)
- [x] Isaac ROS 5.0 (Lyrical) via `isaac-ros/release-5.0 noble-jetpack` + NVIDIA's Lyrical Buildfarm for noble:
      profile `jp721-lyrical-isaac`, test package with Isaac ROS TensorRT + `rosidl::Buffer` CUDA backend (F30-F32)
      (2026-11-02). Isaac ROS 4.x/Jazzy (no arm64 debs, F29) stays a source build if needed
- [x] On the Nano: `jxb test --board` 8/8, deploy + run SAM object_detection on the GPU (2026-11-03, F36-F38)
- [x] Remote gdb on the Nano (`jxb debug`, F42) and the nsys capture round trip (`jxb trace nsys`, F46)
      (2026-11-03)
- [x] Board runtime for Kilted: shipped to `~/jxb/<profile>/runtime` instead of installed, so the shared board's
      apt stays untouched (F44) (2026-11-03)
- [x] Success: `object_detection_package` builds in xbuild and runs on the Nano (GPU) without on-board building
      (2026-11-03)

### Phase 1b: Board workflows (next, 2026-11-03)
Like the Renesas RDK's VS Code tasks/launch configs, but as `jxb` commands first; the VS Code files only call
`jxb`, so the same workflows work from the terminal, from VS Code and later from JetsonPlanner.
- [x] All profiles on the Nano: Jazzy native, Kilted and Lyrical-on-Noble as portable runtime under
      `~/jxb/<profile>` (no apt on the shared board), Lyrical (26.04) as container (`docker`, `nvidia` is in the
      docker group). Kilted (tests 14 + 9) and Lyrical + Isaac ROS 5.0 (15 + 10) from the shipped
      `/opt/ros/<distro>`, SAM `object_detection` on the GPU under both (F44). Lyrical 26.04 in `jxb-rt:<profile>`
      (arm64 image imported from the sysroot on the host, GPU via CDI): tests 15 + 6, the three ONNX Runtime GPU
      steps skipped in container mode (F56, F72) (2026-11-04)
- [x] GPU inference benchmark with ONNX Runtime (CUDA EP, TensorRT EP FP16) vs `trtexec` on a SAM YOLO model:
      latency p50/p99, throughput (feeds the inference plan in `quantization-aware-model/PLAN.md`).
      S0 on the Nano: CUDA EP 35.1 ms, TensorRT EP fp32 22.6 ms, **fp16 11.8 ms**, trtexec fp16 10.8 ms
      (findings F41); nsys shows the CUDA EP's synchronous input staging (F46)
- [x] Remote debug: `jxb debug` (gdbserver on the board's loopback through an SSH tunnel, host gdb or
      gdb-multiarch with the sysroot), `--launch` for one node of a launch file, `jxb gdb`/`jxb-gdb` for VS Code,
      `jxb ssh`. Verified on the Nano with SAM `object_detection` (F42)
- [x] Remote trace: `jxb trace nsys|tegrastats|kernel` → `traces/<board>/<stamp>-<kind>/`; live tegrastats and
      live kernel trace (raw `trace_pipe` or per-second scheduler statistics); kernel tracing gets root via sudo
      (password typed by the user), a privileged container or not at all; `jxb trace gui` shows the board's
      `nsys-ui`/`ncu-ui` over SSH X11 (docs/workflows.md). nsys and tegrastats verified on the Nano; the kernel
      trace helper so far only against a mock tracefs (needs one run with root on the board)
- [x] Trace analytics: `jxb trace report` → `summary.json` (GPU kernels/busy time, memcpy, CUDA API, NVTX, OS
      runtime, CPU samples; tegrastats means/max/energy + CSV; per-task CPU time, wakeup latency, IRQs),
      `jxb trace list --json`, JSON-lines live streams
- [x] `jxb vscode init -w <ws>`: Renesas-style tasks and cppdbg configs (build, deploy, run, launch, debug a node
      or one node of a launch file, SSH, traces) in `vscode/<ws>.code-workspace`, so the workspace's own
      `.vscode/` stays untouched (`--into-workspace` writes it there only if absent). Debug flow replayed over MI
      (F47); not yet clicked through in the VS Code GUI
- [ ] Newest SAM develop (d115bc5f, Lyrical on 26.04): builds 25/27 on both Lyrical profiles, object_detection
      finds the CUDA EP plugin from jxb's ORT on the Nano (with a path override). Open: ros2_control/Gazebo in
      the sysroots (size decision), SAM fixes for `ekf_core` install and the plugin path (F55)
- [ ] CUDA kernel debugging: `cuda-gdbserver` is on the board; needs a host `cuda-gdb` that supports aarch64
      targets (check NVIDIA's x86 package) and a launch config for it
- [x] Renesas-style launch mode: `jxb launch` (deploy of selected packages + the workspace's launch/config/
      assets files, `ros2 launch` of a package's or the workspace's launch file, clean SIGINT stop, process groups),
      `--monitor` (tegrastats + per-process CPU/RSS by ancestry), `--bag` (+ CSV of numeric topics), `--with`
      helpers (synthetic camera `tools/board/jxb_image_pub.py`), VS Code tasks with module/argument settings
- [x] Native or container for every board command (`--mode`): SAM's detector verified natively on Jazzy and on
      Kilted (shipped ROS), and in the Kilted runtime container with TensorRT (within the run-to-run variation of native runs; F50,
      F56, F63). ONNX Runtime's CUDA EP inside the container reset the board once: open, needs root to read pstore
- [x] Lyrical runtimes: bag recording (`--topics`) and clean SIGINT shutdown in the 26.04 container; `--monitor`
      records `/proc/meminfo`, `--mem-guard`, `--rates`; 25-minute stack run: memory saturates, no leak
      (2026-11-04, F68-F71)
- [x] Inference baselines (`jxb trace bench`): ONNX Runtime CPU/CUDA/TensorRT EP vs. trtexec engines, fp32/fp16 and
      INT8 (PTQ/QAT/full INT8, YOLO26n) (F49); SAM node: ONNX Runtime 46 ms vs. TensorRT fp16 17 ms vs. INT8 15 ms,
      -0.9 GB RAM, -1 W (F50); plots and comparisons: `jxb trace plot|compare`; board lock for shared measurements
- [x] Full SAM stack on the Nano (`launch/launch.py module:=parking`, Kilted native + container): the first runs
      found undeclared launch dependencies (topic_tools, foxglove_bridge, rplidar_ros, mavros; F52, F60), now added
      by `jxb-rosdeps`; all 12 processes run and stop cleanly natively and in the container (F59), a 25-minute run
      shows no memory leak (F71); analysis in docs/latex/measurements.pdf (2026-11-03/04)
- [ ] Kernel trace of the stack (`jxb trace kernel`): needs root on the board (sudo password typed by the user)

### Phase 2: Fleet and tracing
- [ ] `orin-agent` v0 (health, deploy, run, logs, trace) + CLI on top
- [ ] Multi-board deploy and concurrent debugging with ≥2 boards
- [ ] Synchronized nsys + ros2_tracing + tegrastats capture on N boards, Perfetto reports
- [x] Lyrical mode (a): 26.04 Lyrical container on JetPack, GPU through the board's CDI spec; CUDA/cuDNN/TensorRT
      come from the board via nvidia-container-toolkit (no CUDA 13.2/TensorRT for 26.04 sbsa, findings F23); hybrid
      sysroot (26.04 + Lyrical + r39.2 dev debs); deployment in a mounted volume. Verified on the Nano: test suite,
      SAM's detector with TensorRT, bag recording and clean shutdown (F68, F69, F72) (2026-11-04)
- [ ] CI: shellcheck, image builds, sysroot build, proof packages

### Phase 3: Golden image (stock JetPack + bundle)
- [ ] Bundle build: apt repo + container images + config, versioned with the sysroot manifest
- [ ] `provision` command: fresh stock JetPack Nano → fully provisioned in one run
- [ ] Optional clone/massflash image from a provisioned board
- [ ] Bundle contains SAM stack + agent + tracing tools; measure RAM baseline on the Nano

### Phase 4: Multi-distro
- [x] Profiles per distro and mode (Jazzy/Kilted native, Lyrical container, Lyrical native); switch = new host
      image + sysroot (+ bundle, Phase 3). Build side 2026-11-02; on the board: Jazzy native, Kilted and Lyrical on
      Noble shipped, Lyrical 26.04 in its runtime container (2026-11-04). A Lyrical source build was superseded by
      NVIDIA's Isaac ROS Buildfarm (D2)
- [x] Lyrical mode (b): native Lyrical on 24.04 from NVIDIA's Isaac ROS Buildfarm (`jp721-lyrical-isaac`, build side
      done 2026-11-02, tests 13/13); runs on the Nano without installing anything (shipped runtime, F44). Against
      mode (a): the detector node holds 0.63 vs. 0.68 GB, the shipped runtime takes 226 MB on disk vs. 10.8 GB for
      the runtime image (2026-11-04)
- [x] Document the supported matrix (L4T × Ubuntu × ROS) for L4T 39.2.1: README "Profiles", docs/system.md
      Section 3.3 (2026-11-04)

### Phase 5: JetsonPlanner (postponed, separate project)
JetsonPlanner (C++/Qt 6) is developed as its own repository later. This project stays integrable:
- every `jxb` operation is a non-interactive command with machine-readable output (`--json`), so the GUI can drive
  it as a backend before the agent exists;
- profiles (`profiles/*.toml`), board inventory (`boards.toml`) and artifacts (`traces/`, sysroot `.jxb/` metadata)
  are the shared data model, documented as such;
- the target-side `orin-agent` API (Phase 2) is defined once (protobuf) and used by both `jxb` and JetsonPlanner.

### Bonus (when the core phases are done)
- [ ] W8 remote GUI: check the Nano's desktop session (Wayland/Xorg); `board gui` for single windows over SSH;
      on-demand RDP desktop; measure RAM/CPU cost

---

## 5. Open decisions

| # | Decision | Options | Recommendation |
|---|---|---|---|
| D1 | Board OS base | — | **decided: stock JetPack 7.2.1 (Ubuntu 24.04)** |
| D2 | How Lyrical (SAM) runs on a 24.04 board | — | **decided: both, as two board modes.** (a) Lyrical container (26.04 base, `--runtime nvidia`), profile `jp721-lyrical`; (b) Lyrical natively on 24.04: since Isaac ROS 5.0 (2026-09-21) NVIDIA publishes Lyrical for Noble on its Isaac ROS Buildfarm, profile `jp721-lyrical-isaac` (no own ROS source build needed). The profile selects the mode; the CLI, agent and tracing work the same in both |
| D3 | Distro profiles | Jazzy (Isaac ROS), Kilted, Lyrical | first profile: 24.04 native (Jazzy + Isaac ROS, Kilted); Lyrical via D2 second |
| D4 | Image build system | — | **decided by D1: no own rootfs**; bundle on stock JetPack (W4) |
| D5 | Agent / GUI language | — | **decided: C++**, Qt 6/QML for the GUI |

## 6. Questions for the team
1. Is the SAM stack on the Nano today native or in a container, and which image is used there?
2. Must the SAM stack stay on Lyrical, or could the board side run Jazzy/Kilted?
3. Are build servers stronger than the 20-core / 15 GB workstation available (e.g. on the HM network)?
4. When will more Orins (more Nanos, AGX) be added to the fleet?
