# Changelog

`jxb --version` prints the version and the commit; every recording's `meta.json` notes it (`jxb`).

## 1.1.1 (2026-11-12)

- A board that resets during a command no longer leaves jxb waiting: SSH keepalives fail the connection within about a
  minute, and jxb reports whether the board rebooted (its uptime) instead of a login error.
- `jxb board check` shows the uptime, whether the last boot followed a kernel crash (pstore record times, no root),
  free memory and the measurement lock.
- F85: a third board reset shows the same kernel fault as F79 from another code path: the RT kernel's memory-cgroup
  accounting, not only memory reclaim. Docs updated accordingly.
- `AGENTS.md` (with `CLAUDE.md` importing it): a short guide for AI coding agents: commands, repository map, the rules
  for the shared board and the shared checkout, conventions, pitfalls. The outdated `HANDOFF.md` moved to `attic/`.

## 1.1.0 (2026-11-10)

- **ROS 2 tracing** with LTTng, no root: `jxb launch --ros-trace` and `jxb trace ros`. The report gives per callback
  (node, topic, timer or service, C++ symbol) the count and the mean, p99 and max durations; per topic the publish rate
  and the age of taken messages (DDS source timestamp to take). Timelines show the callbacks per thread. Native mode
  only for now (F83).
- Findings F83 (the detector works on 0.32 s old frames: a keep-last-10 queue drained at 5 Hz) and F84 (a second board
  reset, most likely the reclaim bug of F79, while the full stack started).

## 1.0.0 (2026-11-10)

The first complete release: cross-development, deployment, debugging and analysis of ROS 2 / CUDA / TensorRT
software on Jetson Orin boards that stay on stock JetPack 7.2.1. Verified on an Orin Nano 8 GB.

**Build**
- Profiles for JetPack 7.2.1: Jazzy, Kilted, Lyrical + Isaac ROS 5 (Ubuntu 24.04) and Lyrical on Ubuntu 26.04.
- Sysroots built from the board's own apt repositories without emulation, pinned by a package lock; workspace and
  launch-file dependencies (`jxb sysroot rosdeps`); ONNX Runtime 1.31 with CUDA and TensorRT EPs, acados, depthai.
- An xbuild image per profile (cross GCC, nvcc 13.2 for the board, ROS tools, cuda-gdb), a long-lived dev container,
  `cross-colcon-build`, binary checks against the board's library versions.

**Board**
- `deploy`, `run`, `ssh`, Renesas-style `launch` (clean Ctrl-C), natively or in a GPU-enabled runtime container; only
  `~/jxb/` is written on the board.
- `debug` with gdbserver over SSH, also for one node of a launch file; `--cuda` for CUDA kernels.
- `gui`: a board program's window on the desktop over X11.
- Several boards at once: `NAME,NAME`, `@GROUP`, `all`; a ROS domain per board.

**Analysis**
- Recordings: Nsight Systems, tegrastats, kernel traces (ftrace), inference benchmarks, `launch --monitor` (per-process
  CPU and memory, memory counters), `--bag`, `--rates`, `--mem-guard`, helpers such as a synthetic camera.
- Valid measurements on shared boards: a measurement lock, the board's clocks and temperatures at start and end,
  a clock offset per board for a common time axis, tegrastats' real sampling interval, ring-buffer loss in kernel
  traces reported and handled.
- `report` and `summary.json`, `plot`, `compare`, and Perfetto timelines across recordings and boards.

**Tools and documentation**
- VS Code workspace with tasks and debug configurations (CUDA, several boards).
- CI: shell and Python checks, analysis tests and CLI dry runs on every push; image builds for every profile; a
  self-hosted workflow for sysroots and the proof packages.
- [Getting started](docs/getting-started.md), [analyst's guide](docs/analysis.md), [workflows](docs/workflows.md),
  [system architecture](docs/system.md) ([PDF](docs/latex/jxb.pdf)), the SAM stack's
  [measurement report](docs/latex/measurements.pdf) and 82 [findings](docs/findings.md).

**Known limitations**: see [PLAN.md](PLAN.md) and the [system document](docs/system.md), Section 12.
