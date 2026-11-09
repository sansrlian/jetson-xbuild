# Workflows: deploy, run, debug, trace (CLI and VS Code)

Everything goes through `jxb` over SSH with keys (no passwords stored anywhere). Boards stay stock JetPack: jxb
writes only below `~/jxb/` on the board and installs nothing there.

## The loop

| Step | CLI | VS Code (`jxb vscode init`) |
|---|---|---|
| build | `jxb build [colcon args]` | task `jxb: build` (default build task) |
| build with debug info | `jxb build --packages-up-to P --cmake-args -DCMAKE_BUILD_TYPE=RelWithDebInfo` | `jxb: build package (debug info)` |
| deploy | `jxb deploy BOARD --ws WS [--packages P ...]` | `jxb: deploy`, `jxb: build + deploy` |
| run a node | `jxb run BOARD -- ros2 run P E` | `jxb: run node on board` |
| launch file (deploy + launch) | `jxb launch BOARD -- LP LF [ARGS]` or `-- launch/launch.py ARGS` | `jxb: launch on board` |
| launch, recorded | `jxb launch BOARD --monitor [--bag T ...] [--with CMD] --duration S -- ...` | `jxb: launch + monitor (recorded)`, `... + bag` |
| debug a node | `jxb debug BOARD --pkg P --exe E` | Run and Debug: `jxb: debug node on board` |
| debug one node of a launch file | `jxb debug BOARD --pkg P --exe E --launch LP LF` | `jxb: debug node in launch file on board` |
| shell on the board | `jxb ssh BOARD` | `jxb: ssh to board` |
| GPU/CPU profile | `jxb trace nsys BOARD [--tegrastats] -- CMD` | `jxb: trace nsys node`, `jxb: trace nsys launch` |
| load, clocks, power, temperatures | `jxb trace tegrastats BOARD [--live]` | `jxb: tegrastats live` |
| kernel trace (scheduler, IRQs) | `jxb trace kernel BOARD [--live [--top]]` | `jxb: kernel trace`, `jxb: kernel trace live (top)` |
| inference benchmark | `jxb trace bench BOARD --models M.onnx ...` | `jxb: benchmark models` |
| analysis | `jxb trace report [BOARD\|DIR]`, `jxb trace list` | `jxb: trace report (latest)` |
| plots, comparisons | `jxb trace plot [DIR ...]`, `jxb trace compare DIR ...` | `jxb: trace plot (latest)` |
| Nsight GUI of the board | `jxb trace gui BOARD [--tool ncu]` | — |
| versions board vs. sysroot | `jxb board check BOARD` | `jxb: board check` |

How this maps to the Renesas RDK's VS Code setup:

| RDK (`.vscode/tasks.json`, `launch.json`) | jxb |
|---|---|
| `ROS2: Build Release` / `Build Debug` (`cross-colcon-build`) | `jxb: build` / `jxb: build package (debug info)` |
| `ROS2: Deploy to Target` | `jxb: deploy` (also ships jxb's runtime, e.g. ONNX Runtime with CUDA/TensorRT EP) |
| `ROS2: SSH to Target` (`sshpass`, password in `settings.json`) | `jxb: ssh to board` (keys only) |
| `ROS2: Run Package Executable` / `Launch Package LaunchFile` | `jxb: run node on board` / `jxb: launch on board` |
| SAM `ROS: Start Launch-File` (platform, module, log level) | `jxb: launch on board` with `jxb.launchFile = launch/launch.py`, `jxb.launchArgs = module:=core log_level:=INFO` |
| `ROS2: Debug Run (GDB)` + `GDB for ROS2 Run` | `jxb: gdbserver node` + `jxb: debug node on board` |
| `ROS2: Debug Launch (GDB)` + `GDB for ROS2 Launch` | `jxb: gdbserver node in launch` + `jxb: debug node in launch file on board` |
| `ROS2: Check Package Versions (Sysroot and Board)` | `jxb: board check` |
| `Install Deps on Target (rosdep)`, `Purge ROS Deb Packages on Target` | none on purpose: the board stays stock, missing runtime pieces are shipped to `~/jxb/<profile>/runtime` |
| settings `NODE_PACKAGE_NAME`, `NODE_EXECUTABLE_NAME`, `TARGET_IP`, ... | settings `jxb.package`, `jxb.executable`, `jxb.board` (address from `boards.toml`) |

## Launch mode (Renesas style)

`jxb launch` deploys the workspace and runs a launch file on the board, in the terminal or as a VS Code task:

```bash
cd ~/ros2_ws
# a package's launch file; only this package is deployed
../jetson-orin-xbuild/jxb launch nano-lab --packages object_detection_package -- object_detection_package object_detection_launch.py
# a launch file of the workspace (SAM: launch/launch.py with platform and module)
../jetson-orin-xbuild/jxb --profile jp721-kilted launch nano-lab -- launch/launch.py module:=core log_level:=INFO
# recorded: per-node CPU/RAM + tegrastats, a bag, and a synthetic camera instead of the hardware
../jetson-orin-xbuild/jxb launch nano-lab --monitor --duration 60 --bag /object_detection/inference_time \
    --with "python3 {tools}/jxb_image_pub.py --rate 30 /fisheye_undistortion_node/image_raw" \
    -- object_detection_package object_detection_launch.py model:=model.onnx
```

- Deploy: `install-<profile>/` (or `--packages`), plus the workspace's run-time files: `launch/`, `assets/`,
  `src/*/{launch,config,params,urdf}/`. SAM's launch files include each other by path below
  `$WORKSPACE_ROOT/src/`, and jxb sets `WORKSPACE_ROOT` to the deployed workspace. Git LFS pointer files (objects
  not pulled) are skipped, so they never replace real models on the board. Deploy also ships what the board lacks
  for the profile: its ROS distro (from the sysroot) and libraries ldd can't resolve go to
  `~/jxb/<profile>/runtime/{opt/ros,syslibs}`, and container profiles get `jxb-rt:<profile>` once per sysroot
  build.
- Ctrl-C or the end of `--duration` stops the launch with SIGINT, so the nodes shut down cleanly; helpers
  (`--with`) and the bag recorder stop afterwards.
- `--monitor` samples every process's CPU and RSS each second and attributes them to the session by ancestry
  (the session's shell or its runtime container), so other users' processes on a shared board don't count. It
  also samples the system's memory counters (`/proc/meminfo`: `MemAvailable`, `Cached`, `Shmem`, `Slab`, ...), for
  memory that no process's RSS shows; the report prints their trends in MB/min and writes `meminfo.csv`.
- `--mem-guard MB` stops the launch when the board's `MemAvailable` falls below MB (SIGINT every 5 s, SIGKILL
  after 20 s, noted in `memguard.log`), so a leaking stack can't push a shared board into the out-of-memory killer.
- `--rates TOPIC ...` measures message rates once a second in one probe process (`tools/board/jxb_rate_probe.py`,
  the publishers' reliability; results in `rates.log` and the report). Receiving large messages costs the probe their
  transport (about a quarter of a core for 1280x800 images at 30 Hz): for image streams, probe their camera info.
- `--bag TOPIC ...` records MCAP; numeric topics (e.g. `inference_time`) and header latencies become CSV for the
  analysis. `--bag` without topics records everything, including images: that gets big quickly.
- `tools/board/jxb_image_pub.py` is a synthetic camera (road scene, lane markings, moving objects; optional
  `--camera-info` with a fisheye model) for driving perception nodes without hardware or recordings.
- Workspace settings in `jxb.toml`:

  ```toml
  [workspace.ros2]                                           # by the workspace directory's name
  env = { ROS_AUTOMATIC_DISCOVERY_RANGE = "LOCALHOST" }      # exported on the board, any mode
  container_env = { RMW_IMPLEMENTATION = "rmw_cyclonedds_cpp" }   # only in the runtime container
  # files = ["launch/", "assets/", "src/*/launch/"]         # replaces the default run-time files
  ```

## Native or container: `--mode`

Every board command (`deploy`, `run`, `launch`, `debug`, `ssh`, `test --board`, `trace`) runs either natively on
the board's stock JetPack or in the profile's runtime container:

- `native`: the board's `/opt/ros/<distro>`, or the distro shipped from the sysroot when the board lacks it
  (Kilted and Lyrical-on-Noble on a Jazzy board). The profile must target the board's Ubuntu release.
- `container`: `jxb-rt:<profile>`, an arm64 image made from the profile's sysroot on the PC (no emulation:
  `docker import` of the sysroot without headers and static libraries; `jxb image runtime` builds it ahead),
  loaded on the board once per sysroot build. GPU through the board's CDI spec, host network and IPC (DDS), the
  deployment mounted at the same path. That's the way to run SAM "as on the PC" (Ubuntu 24.04 + Kilted container)
  and the only way for profiles built for another Ubuntu release (Lyrical on 26.04).
- `auto` (default, or `mode = "..."` in `jxb.toml`): container only when the profile's Ubuntu release differs
  from the board's.

Caveat: the Nano reset once while ONNX Runtime's CUDA EP initialized inside the Kilted runtime container (findings
F56); TensorRT engines in the container run fine. Until the cause is known, run ONNX Runtime with the CUDA EP natively.

Composable nodes: SAM loads most nodes into one `component_container_mt` (`use_global_composition:=true`, the
default of `launch/launch.py`). `--monitor` then shows that container as one process; launch with
`use_global_composition:=false` for per-node CPU and RAM. To debug a component, the program is the container
(`<sysroot>/opt/ros/<distro>/lib/rclcpp_components/component_container_mt`, `--exe component_container_mt` with
`--launch`); the component libraries resolve through the workspace's install tree.

## VS Code

```bash
./jxb vscode init -w ~/ros2_ws --board nano-lab --pkg object_detection_package --exe object_detection \
    --launch object_detection_package object_detection_launch.py
code vscode/ros2_ws.code-workspace
```

This writes a multi-root workspace (the ROS 2 workspace + this repo) to `vscode/<ws>.code-workspace` (gitignored:
it is generated and local), so the workspace's own `.vscode/` stays as it is. Running `init` again regenerates
the tasks and debug configurations and keeps the settings. `--into-workspace` writes
`<ws>/.vscode/{tasks,launch,settings}.json` instead, but only if none of them exists.

| Setting | Meaning |
|---|---|
| `jxb.profile` | profile (ROS distro, JetPack) the tasks build and deploy for |
| `jxb.mode` | `auto`, `native` or `container` (see above) |
| `jxb.board` | board from `boards.toml` |
| `jxb.package`, `jxb.executable` | node to run, debug and trace |
| `jxb.launchPackage`, `jxb.launchFile` | launch file for the launch tasks and launch debugging; with an empty package, a launch file of the workspace (`launch/launch.py`) |
| `jxb.launchArgs` | launch arguments, e.g. `module:=core log_level:=INFO` |
| `jxb.launchWith` | helper next to recorded launches, e.g. `python3 {tools}/jxb_image_pub.py --rate 30 /camera/color/image_raw` |
| `jxb.bagTopics` | topics for `launch + monitor + bag` (empty: all) |
| `jxb.benchModels` | ONNX files for `benchmark models` |
| `jxb.gdbPort` | local and board port of the gdbserver tunnel (`boards.toml` `gdb_port`, default 3000) |
| `jxb.traceSeconds` | duration of the trace tasks |

Debugging, under the hood:

- The preLaunchTask runs `jxb debug ... --server-only`: gdbserver starts on the board bound to `127.0.0.1` and is
  reached through the SSH session that started it (`ssh -L`), so nothing is exposed on the lab network. VS Code
  starts the debugger when the task prints `Listening on port`.
- cppdbg's debugger is `toolchain/bin/jxb-gdb`: the host gdb if it supports aarch64, otherwise `gdb-multiarch`
  in the dev container. It sets the profile's sysroot and the workspace's library paths. Sources and binaries
  have the same paths on the host and in the container, so breakpoints in the editor just work.
- Launch-file debugging runs `ros2 launch` with `--launch-prefix 'gdbserver ...' --launch-prefix-filter '^E$'`:
  only that executable runs under gdbserver. A postDebugTask ends the other nodes when the session ends.
- Attaching to an already running node needs root on the board (`kernel.yama.ptrace_scope = 1`); start it under
  gdbserver instead.
- `jxb debug` keeps a terminal on the board (`ssh -tt`): closing the connection hangs up gdbserver and, with
  `--launch`, the launch's other nodes, so nothing keeps running. In container mode the board's
  `/usr/bin/gdbserver` is mounted into the runtime container.
- gdb's warning `.dynamic section ... not at the expected address` for `libblas.so.3`/`liblapack.so.3` is harmless:
  the sysroot's alternatives pick OpenBLAS, the board has the reference libraries (findings F43).

## Tracing

Recordings land in `traces/<board>/<stamp>-<kind>[-<label>]/` (stamp from the host clock: boards may run without
NTP), together with `meta.json` and the analysis in `summary.json`. Raw files are removed from the board after
fetching (`--keep` keeps them).

Every recording (launch, nsys, tegrastats, kernel, bench) also notes the board's state at its start and its end,
inside the measurement lock (`board_state_start.txt`, `board_state_end.txt`; sysfs, no root, ~25 ms):

- power mode (`nvpmodel`)
- CPU and devfreq governors, limits and current clocks (GPU, EMC = `bwmgr`, the video and vision engines)
- disabled CPU idle states, GPU rail gating
- fan control and temperatures

The report prints it in one line. It lists clocks held at one frequency (minimum = maximum, as `jetson_clocks`
sets them) and the settings that changed during the recording. `jxb trace compare` lists the settings that differ
between recordings, since their results aren't directly comparable.

### Nsight Systems: `jxb trace nsys`

```bash
./jxb trace nsys nano-lab --duration 10 --tegrastats --label detector -- ros2 run object_detection_package object_detection
```

Runs `nsys profile` on the board with the deployment's environment (CUDA, NVTX, OS runtime, cuBLAS, cuDNN; CPU
sampling and context switches of the process tree, which needs no root), exports SQLite on the board and fetches
`report.nsys-rep` + `report.sqlite`. The report shows the top GPU kernels and GPU busy time, memcpy volume per
direction, the CUDA API, NVTX ranges, OS runtime calls and the hottest CPU functions. Ctrl-C ends the recording
and keeps the report. `--nsys-arg` passes further `nsys profile` options.

To look at the timeline, open `report.nsys-rep` in Nsight Systems on the PC (NVIDIA's host package; it can also
profile the board interactively over SSH), or run the board's own GUI here: `jxb trace gui nano-lab` shows its
`nsys-ui` as a single window over SSH X11 forwarding (needs an X server or XWayland on the PC).

### tegrastats: `jxb trace tegrastats`

```bash
./jxb trace tegrastats nano-lab --live                      # live view, Ctrl-C ends
./jxb trace tegrastats nano-lab --duration 30               # recording -> tegrastats.log, tegrastats.csv
./jxb trace tegrastats nano-lab -- ros2 launch pkg file.py   # recording while a command runs
```

The analysis gives mean/max of CPU and GPU load, clocks, RAM, temperatures and power rails, and the board's
input energy (`VDD_IN` integrated over the recording). `--tegrastats` on `nsys` and `kernel` records it alongside.

### Kernel trace: `jxb trace kernel`

```bash
./jxb trace kernel nano-lab --duration 10                   # record -> kernel.ftrace.txt
./jxb trace kernel nano-lab --live --top                    # per-second scheduler statistics
./jxb trace kernel nano-lab --live                          # raw trace_pipe
./jxb trace kernel nano-lab --duration 20 -- ros2 run pkg node   # trace while a workload runs
```

Plain ftrace through tracefs, driven by `tools/board/jxb-ftrace.sh` (copied next to the recordings); no extra
tools on the board. Event presets: `sched` (switch, wakeup, migrate, exec/exit), `irq` (hard and soft IRQs),
`power` (CPU frequency and idle), `syscalls`, `workqueue`, or any tracefs event like `sched/sched_switch`. The
helper restores the previous tracing state on exit, also on Ctrl-C.

The analysis lists per task and per process the CPU time, context switches, preemptions and **wakeup latency**
(`sched_wakeup` to running; relevant on the board's PREEMPT_RT kernel, e.g. for ROS executor threads), per-CPU
busy time and IRQ/softirq handler times. `kernel.ftrace.txt` opens in [Perfetto](https://ui.perfetto.dev) as a
timeline.

tracefs is root-only on JetPack. Three ways, chosen with `--root` or `trace_root` in `boards.toml`:

| `--root` | How | Note |
|---|---|---|
| `sudo` (default) | sudo on the board asks for the password in your terminal | jxb never handles the password; VS Code task terminals work too |
| `docker` | a privileged container runs the helper (`trace_image = "<image with /bin/sh>"` in `boards.toml`) | for users in the board's `docker` group (root-equivalent), no password; on a shared board only with the owner's explicit OK |
| `none` | runs the helper as the SSH user | when tracefs was made accessible on the board |

### Inference benchmarks: `jxb trace bench`

```bash
./jxb trace bench nano-lab --models models/S0_fp32_ref/best.onnx models/S2_qat_finetune_s/best_int8.onnx
```

Every model with ONNX Runtime (jxb's build: CPU, CUDA EP, TensorRT EP fp32/fp16, INT8 for Q/DQ models) and as a
native TensorRT engine (trtexec fp32/fp16, INT8 for Q/DQ models): latency p50/p90/p99, FPS and the speedup against
ONNX Runtime's CUDA EP, with tegrastats alongside. `--backends` selects a subset. The engines stay on the board in
`~/jxb/<profile>/bench/engines/` and can be used directly, e.g. by SAM's detector (`model:=<path>.engine`).

### Plots and comparisons: `jxb trace plot`, `jxb trace compare`

`jxb trace plot [DIR|BOARD|latest ...]` writes `plots/*.png` and `report.md` into each recording: telemetry
(CPU, GPU, EMC, RAM, power rails, temperatures), per-process CPU/RSS over time, benchmark bars, top CUDA kernels,
scheduler statistics, and the distributions of bag topics such as the inference time. `jxb trace compare DIR ...
--name NAME` puts recordings side by side (e.g. native vs. container, ONNX Runtime vs. TensorRT) in
`traces/compare/NAME/compare.md` + `.png` + `.json`. Both use matplotlib in the dev container.

### Shared boards: the measurement lock

Recorded commands (`trace nsys|tegrastats|kernel|bench`, `launch --monitor`) take `~/jxb/.measure.lock` on the
board (flock) for their duration, and write the holder to `~/jxb/.measure.lock.info`. Measurements of several users
or agents then run one after another instead of measuring each other's load; a waiting command says who holds the
board. Other tools can take part: `flock -w 3600 ~/jxb/.measure.lock <command>`. `--no-lock` opts out.

### What needs root on a stock JetPack board

| Feature | Root |
|---|---|
| `nsys` with process-tree sampling, CUDA/NVTX/OS runtime trace | no |
| `nsys` system-wide sampling, `--ftrace` | yes |
| `tegrastats` | no |
| kernel trace (tracefs) | yes |
| Nsight Compute (GPU performance counters) | yes |
| gdbserver starting the program (also from a launch file) | no |
| gdbserver attaching to a running process | yes (`ptrace_scope = 1`) |

## Machine-readable results (JetsonPlanner)

All commands are non-interactive with fixed paths, so a GUI can drive them and read the results:

- `jxb trace list --json`: recordings with board, kind, label, command, duration and exit code.
- `jxb trace report DIR|BOARD|latest --json`: the analysis; also stored as `summary.json` in the trace directory
  (keys `meta`, `board_state`, `nsys`, `tegrastats`, `kernel`, ..., each present when that recording has it;
  docs/system.md, Appendix C).
- `jxb trace tegrastats BOARD --live --json`: one JSON object per sample (JSON lines).
- `jxb trace kernel BOARD --live --top --json`: one JSON object per second of trace time.
- Recording commands take `--json` to print `summary.json` at the end instead of the text report.
