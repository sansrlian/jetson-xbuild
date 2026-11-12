# Getting started with jxb

jxb lets you develop ROS 2 software for NVIDIA Jetson Orin boards on your x86_64 PC. It cross-builds your workspace
(CUDA, TensorRT, ONNX Runtime included) against an exact copy of the board's software, deploys it to one or more
boards over SSH, runs and debugs it there, and records performance data. Boards keep their stock JetPack: jxb
installs nothing on them and writes only to `~/jxb/`.

## What it does

| You want to | Command | |
|---|---|---|
| Cross-build a ROS 2 workspace | `jxb build` | colcon in a dev container; output in `build-<profile>/`, `install-<profile>/` |
| Pick the ROS distro / JetPack | `jxb --profile NAME ...` | Jazzy, Kilted, Lyrical (24.04 + Isaac ROS 5), Lyrical (26.04) |
| Copy the build to a board | `jxb deploy BOARD` | also ships what the board lacks (a ROS distro, libraries, ONNX Runtime with GPU support) |
| Start a launch file on the board | `jxb launch BOARD -- PKG FILE` | deploys first; Ctrl-C stops all nodes cleanly |
| Run one command on the board | `jxb run BOARD -- CMD` | with ROS and your workspace sourced |
| Open a shell on the board | `jxb ssh BOARD` | same environment |
| Run natively or in Docker | `jxb --mode native\|container ...` | container = the profile's runtime image, GPU included |
| Debug a node | `jxb debug BOARD --pkg P --exe E` | gdbserver on the board, gdb here with full symbols |
| Debug CUDA code | `jxb debug BOARD --pkg P --exe E --cuda` | cuda-gdbserver + cuda-gdb; GPU code needs the board user in group `debug` |
| Show a board program's window here | `jxb gui BOARD -- rqt` | X11 over SSH, no desktop session on the board |
| Work on several boards at once | `jxb deploy a,b`, `@group`, `all` | in parallel, with a result table; also run, launch, trace |
| Work in VS Code | `jxb vscode init` | tasks for build, deploy, launch, trace; debug configurations |
| Record load, power, GPU | `jxb launch --monitor`, `jxb trace ...` | Nsight Systems, tegrastats, kernel trace, benchmarks |
| Look at the results | `jxb trace report\|plot\|compare` | text, `summary.json`, plots |
| One timeline of recordings | `jxb trace perfetto ...` | for ui.perfetto.dev; several boards on one time axis |
| ROS 2 callback timing | `jxb launch --ros-trace`, `jxb trace ros` | per callback and topic, with LTTng (no root) |

Profiles (all for JetPack 7.2.1, CUDA 13.2, TensorRT 10.16):

| Profile | ROS 2 | On a stock Jazzy board |
|---|---|---|
| `jp721-jazzy` | Jazzy | uses the board's own ROS |
| `jp721-kilted` | Kilted | ships Kilted to `~/jxb/jp721-kilted/runtime` (or `--mode container`) |
| `jp721-lyrical-isaac` | Lyrical + Isaac ROS 5.0 (NVIDIA buildfarm, Ubuntu 24.04) | ships Lyrical |
| `jp721-lyrical` | Lyrical on Ubuntu 26.04 | always in its container |

## 1. Set up once

**On the PC** (Linux x86_64): Docker (your user in the `docker` group), git, git-lfs, rsync, OpenSSH, zstd and
Python 3.11 or newer. Plan about 20 GB of disk per profile (sysroot ~16-19 GB, build image ~3.5 GB), plus ~10 GB
per profile you run in container mode.

```bash
git clone https://github.com/sansrlian/jetson-xbuild.git ~/jetson-orin-xbuild
cd ~/jetson-orin-xbuild
alias jxb=~/jetson-orin-xbuild/jxb              # fish: alias jxb ~/jetson-orin-xbuild/jxb
```

**Boards:** copy `boards.example.toml` to `boards.toml` and enter each board's address. jxb uses SSH keys only;
set them up once per board:

```bash
cp boards.example.toml boards.toml               # then edit host = "..."
ssh-copy-id nvidia@192.168.178.83
```

**Defaults:** `jxb.toml` (local, not committed) saves typing:

```toml
profile = "jp721-kilted"                         # default for --profile
workspaces = ["/home/me/ros2_ws"]                # mounted into the dev container

[sysroot]
extra_sets = ["ws-{profile}-ros2_ws"]            # your workspace's dependencies, see below

[workspace.ros2_ws]                              # settings for that workspace on the board
env = { ROS_AUTOMATIC_DISCOVERY_RANGE = "LOCALHOST" }
```

**Per profile** (each takes a few minutes; the first sysroot downloads ~8 GB of packages):

```bash
jxb --profile jp721-kilted image build           # the cross-build container image
jxb --profile jp721-kilted sysroot rosdeps -w ~/ros2_ws   # your workspace's dependencies (incl. launch files)
jxb --profile jp721-kilted sysroot build         # board software, from the board's own apt repositories
jxb --profile jp721-kilted up -w ~/ros2_ws       # long-lived dev container with your workspace
jxb --profile jp721-kilted test --board nano-lab # check the whole chain: CUDA, TensorRT, ONNX Runtime, ROS
```

Run `sysroot rosdeps` + `sysroot build` again whenever your workspace gets new dependencies.

## 2. Daily use

Work in your workspace directory; jxb finds the workspace from there (or pass `--ws DIR`).

```bash
cd ~/ros2_ws
jxb build --packages-up-to object_detection_package          # cross-build (colcon arguments as usual)
jxb launch nano-lab -- object_detection_package object_detection_launch.py   # deploy + launch, Ctrl-C stops
jxb launch nano-lab --packages object_detection_package -- ...               # deploy only that package
jxb launch nano-lab -- launch/launch.py module:=core log_level:=INFO        # a launch file of the workspace
jxb run nano-lab -- ros2 topic hz /object_detection/inference_time
jxb ssh nano-lab                                              # look around on the board
jxb gui nano-lab -- rqt                                       # a board program's window on your desktop
```

- **Switch the ROS distro:** add `--profile jp721-lyrical-isaac` (or change `profile` in `jxb.toml`). Every
  profile has its own build and install trees, so switching doesn't rebuild the others.
- **Run in Docker instead of natively:** add `--mode container`. It runs the same build in the profile's runtime
  image, like a development container on the PC. Use TensorRT engines rather than ONNX Runtime's CUDA EP there
  (see the notes on shared boards).
- **Debug:** build the package with `--cmake-args -DCMAKE_BUILD_TYPE=RelWithDebInfo`, then
  `jxb debug nano-lab --pkg P --exe E` (add `--launch LP LF` to start it through a launch file). For CUDA code,
  add `--cuda` (cuda-gdb); stopping inside CUDA kernels needs the board user in group `debug` once (jxb tells you).
- **Several boards at once:** give a list, a group or all instead of one board, e.g. `jxb deploy nano-lab,agx-01`,
  `jxb launch @lab -- ...`, `jxb run all -- uptime`. Groups and a ROS domain per board are set in `boards.toml`
  (`groups = ["lab"]`, `ros_domain_id = 11`).
- **Models and data files:** `launch/`, `assets/` and the `launch`/`config` folders of your packages are
  deployed with the build. Files that are only Git LFS pointers are skipped: run `git lfs pull` first.

## 3. VS Code

```bash
jxb vscode init -w ~/ros2_ws --board nano-lab --pkg object_detection_package --exe object_detection
code ~/jetson-orin-xbuild/vscode/ros2_ws.code-workspace
```

This opens your workspace with ready-made tasks (Terminal > Run Task: `jxb: build`, `jxb: deploy`,
`jxb: launch on board`, `jxb: launch + monitor (recorded)`, `jxb: trace ...`) and debug configurations
(Run and Debug: `jxb: debug node on board`). What they act on is set in the workspace file's settings:
`jxb.profile`, `jxb.mode`, `jxb.board`, `jxb.package`, `jxb.executable`, `jxb.launchPackage`, `jxb.launchFile`,
`jxb.launchArgs`. Your workspace's own `.vscode/` stays untouched. Run `init` again after updating jxb; your
settings are kept. `jxb: debug node on board (CUDA)` debugs with cuda-gdb; with `--board A --board B` you get one
debug configuration per board and `jxb: debug node on all boards`, which debugs them together.

## 4. Measure and analyze

```bash
jxb launch nano-lab --monitor --duration 60 --bag /object_detection/inference_time \
    --with "python3 {tools}/jxb_image_pub.py --rate 30 /fisheye_undistortion_node/image_raw" \
    -- object_detection_package object_detection_launch.py
jxb trace tegrastats nano-lab --live                          # CPU, GPU, RAM, power, temperatures
jxb trace nsys nano-lab --duration 20 -- ros2 run P E         # Nsight Systems: GPU kernels, CUDA API, CPU
jxb trace bench nano-lab --models model.onnx                  # ONNX Runtime vs TensorRT, fp32/fp16/INT8
jxb trace list                                                # all recordings
jxb trace report nano-lab                                     # analysis of the latest one
jxb trace plot nano-lab                                       # plots + report.md
jxb trace compare traces/nano-lab/A traces/nano-lab/B --name my-test
jxb trace perfetto traces/nano-lab/A traces/nano-lab/B        # one timeline, for ui.perfetto.dev
```

- `--monitor` records CPU and RAM per node, the system's memory counters and tegrastats; `--bag` records topics,
  numeric ones also as CSV; `--with` starts helpers next to the launch, such as the synthetic camera above for
  testing without hardware.
- `--rates TOPIC ...` measures topic rates in one small probe process; `--mem-guard MB` stops the launch before the
  board runs out of memory (useful for long runs on a shared board).
- Everything lands in `traces/<board>/<date>-<kind>-<label>/` on the PC; `summary.json` there is the
  machine-readable result. Each recording also notes the board's clocks, power mode and temperatures, and its clock
  offset to the PC, so recordings line up in time even on boards whose clock is wrong.
- `jxb trace perfetto` puts recordings on one time axis (CPU, GPU, power, processes, topic rates, GPU kernels). Open
  the file at https://ui.perfetto.dev with "Open trace file". A recording on several boards (`jxb trace ... a,b`)
  writes this timeline by itself.
- Kernel traces (`jxb trace kernel`) need root on the board: run them in a terminal and type the sudo
  password when asked.

More on every command: [workflows.md](workflows.md).

## 5. Shared boards

- jxb never installs packages on a board and never touches `/opt` or other users' files; everything it deploys
  is under `~/jxb/<profile>/` and can be deleted at any time.
- Measurements (`--monitor`, `jxb trace ...`) take the board's lock `~/jxb/.measure.lock`, so they don't overlap
  with someone else's. If the board is busy, jxb says who holds it and waits. Other tools can join in with
  `flock ~/jxb/.measure.lock <command>`.
- Every recording notes the board's power mode, clocks and temperatures at its start and end. The report flags
  fixed clocks (`jetson_clocks`) and changes during the run. If you change clocks for your own runs, hold the
  lock while doing it: store the settings first (`jetson_clocks --store FILE`) and restore them
  (`--restore FILE`) before releasing the lock.
- Don't run ONNX Runtime's CUDA EP inside the runtime container for now: the Nano reset once doing that
  (TensorRT in the container is fine). See findings F56.

## 6. When something goes wrong

| Message | What to do |
|---|---|
| `ssh to nvidia@... failed. Key login needed once` | `ssh-copy-id nvidia@<board>` |
| `container exists without <workspace>` | `jxb up --recreate -w <workspace>` |
| `install-<profile> not found; build first` | `jxb build` in that workspace (or `--ws DIR`) |
| build stops with exit 137 | out of memory on the PC: `jxb exec -- env MAKEFLAGS=-j4 cross-colcon-build --parallel-workers 3` |
| `package 'X' not found` on the board | a dependency only your launch files mention: `jxb sysroot rosdeps -w <ws>` + `jxb sysroot build` |
| `Protobuf parsing failed` for a model | the model is a Git LFS pointer: `git lfs pull`, or copy the model to `~/jxb/<profile>/<ws>/assets/models/` |
| `not deploying incomplete packages` | those packages failed to build; the rest is deployed |
| `... is busy with another measurement` | someone else is measuring; jxb waits up to an hour |
| `jxb board check` shows `DIFF` | the board's package versions differ from the sysroot; minor versions are usually fine |
| `GPU debugging is off for nvidia ...` | once, as root on the board: `sudo usermod -aG debug nvidia`; new SSH sessions then debug CUDA kernels |
| `... has no cuda-gdb` | the profile's image predates CUDA debugging: `jxb --profile <p> image build`, then `jxb up --recreate` |
| `boards A, B need different gdb_port values` | give each board its own `gdb_port` in `boards.toml` to debug them together |
| report says `fixed clocks` or `CHANGED during the recording` | someone ran `jetson_clocks` or changed the power mode; measure again before comparing |
| `the SSH connection to <board> broke off, and the board has been up for only N s` | the board reset during the command (a known kernel bug, F85): repeat it; `jxb board check` shows the crash |
| `no X display on this host` | `jxb gui` needs a desktop session on the PC (`DISPLAY`; on Wayland, XWayland provides one) |

## Read more

- [README](../README.md): what jxb is and how it is built
- [analysis.md](analysis.md): the analyst's guide: which recording answers which question, and how to read it
- [workflows.md](workflows.md): every workflow in detail (launch mode, `--mode`, VS Code, CUDA debugging, several
  boards, tracing, timelines, CI)
- [system.md](system.md): how jxb works inside (also as PDF: `docs/latex/jxb.pdf`)
- [findings.md](findings.md): measured baselines and the pitfalls found along the way
- [PLAN.md](../PLAN.md): roadmap and decisions
