# Analyzing Jetson boards with jxb

A guide for analysts: which question each jxb recording answers, how to read the results, and what keeps
measurements on a shared board valid. Setup is in [getting-started.md](getting-started.md); every option is in
[workflows.md](workflows.md); how the analysis works inside is in [system.md](system.md), Section 7.

Commands below assume your workspace is built and deployed (`jxb build`, `jxb deploy BOARD`) and are run from the
workspace directory, with `jxb` on your PATH (or `./jxb` in the jxb repository).

## Before you measure

- **One measurement at a time.** Every recording takes the board's measurement lock, so others' measurements
  don't run at the same time as yours. If the board is busy, jxb says who holds it and waits.
- **Check the board state.** Each report starts with the board's power mode and clocks. `fixed clocks` (someone ran
  `jetson_clocks`) or `CHANGED during the recording` make results incomparable with other runs. Today's reference
  on the lab Nano is 25 W, CPU and GPU `performance` governors, EMC dynamic (findings F73).
- **Warm up.** The first run after a deploy builds TensorRT engines (minutes) and fills caches; measure the second
  run, or drop the first seconds.
- **Repeat.** Two identical runs differ by about 0.5 ms in the detector's median and 0.14 GB RSS (F63). A difference
  smaller than that needs repeated runs before it means anything.
- **Don't let the probe become the load.** Subscribing to 1280x800 image topics costs a quarter of a core per
  subscriber (F70): measure rates on small companion topics (camera info, inference time) with `--rates`, not on
  the images.
- **Keep memory pressure low.** The board's RT kernel crashed once in memory reclaim under pressure (F79). Use
  `--mem-guard MB` for long runs; it stops the launch before memory runs out.

## Questions and the commands that answer them

| Question | Command | Read |
|---|---|---|
| How fast is my model on this GPU, and which backend wins? | `jxb trace bench B --models m.onnx` | p50/p99 latency, FPS and the speedup of each backend (ONNX Runtime CPU/CUDA/TensorRT EP, TensorRT engines; fp32/fp16/INT8) |
| What does my node or stack cost? | `jxb launch B --monitor --duration 60 -- PKG FILE` | per-process CPU and RSS, board power and energy, GPU load |
| How long does each frame take? | `... --bag /node/inference_time --rates /camera/camera_info` | the numeric topic's p50/p99 (`bag`), topic rates |
| Which callback takes how long, and how old is the data it works on? | `jxb launch B --ros-trace --duration 30 -- PKG FILE` | per callback (node, topic or timer): count, mean/p99/max; per topic: publish rate, take latency |
| Where does the GPU time go? | `jxb trace nsys B --duration 20 --tegrastats -- ros2 launch PKG FILE` | top kernels, GPU busy %, copies, CUDA API calls (allocations per frame!), NVTX ranges |
| Are my threads scheduled in time? What interferes? | `jxb trace kernel B --duration 10` (root) | wakeup latency p50/p99/max per thread, preemptions, IRQ and softirq time per handler |
| Does memory grow or leak? | `jxb launch B --monitor --mem-guard 1200 --duration 1500 -- ...` | `memory:` trends in MB/min (MemAvailable, Cached, Shmem, NvMap), RSS over time |
| Did my change help? Native or container? | `jxb trace compare A B --name my-change` | the side-by-side table; differing board settings |
| What happened when, across sources or boards? | `jxb trace perfetto A B` | one timeline at https://ui.perfetto.dev |
| The same on several boards | `jxb launch nano-01,nano-02 --monitor ...` or `@group`, `all` | one recording per board, one merged timeline |
| What is the board doing right now? | `jxb trace tegrastats B --live`, `jxb trace kernel B --live --top` | live CPU/GPU/power, per-second scheduler statistics |
| Why does it crash or compute wrong values on the GPU? | `jxb debug B --pkg P --exe E --cuda` | breakpoints in host code and CUDA kernels |

`B` is a board from `boards.toml`. Without a camera on the board, `--with "python3 {tools}/jxb_image_pub.py
--rate 30 /camera/image_raw"` feeds a synthetic camera (road scene with moving objects) into the launch.

## Reading the results

Every recording lands in `traces/<board>/<stamp>-<kind>-<label>/` with `report` text, `summary.json` and
(`jxb trace plot`) PNG plots. The numbers mean:

| Value | Meaning |
|---|---|
| `cpu%` of a process or thread | CPU time / wall time; 100 % = one full core (the Nano has six) |
| `alive%` | CPU while the process existed (short-lived helpers) |
| `RSS MB` | resident memory of the process; shared libraries are counted in every process that maps them |
| `GPU %` | tegrastats' GR3D load: share of time the GPU was busy |
| `VDD_IN`, `energy J` | the whole board's power, and its integral over the recording |
| `inference_time` p50/p99 | the node's own per-frame measurement, as published (bag) |
| `wake p99us` | how long a thread waited for a CPU after being woken (sched_wakeup to running), 99th percentile |
| `preempt` | how often the thread was switched out while it still wanted to run |
| GPU `busy %` (nsys) | union of all kernel intervals over the recording: overlapping kernels count once |
| `fixed clocks` | a clock held at one frequency (minimum = maximum), as `jetson_clocks` sets it |

Kernel traces keep a fixed-size ring buffer per CPU. If the report says `the ring buffer kept N% of the events`, the
statistics cover only the window every CPU has, and it says which `--buffer-mb` would keep everything (the full SAM
stack needs ~50 MB per CPU for 30 s). Fewer event groups (`--events sched`) or a shorter `--duration` also help.

## Worked example: the SAM parking stack

What the commands above found on the lab Nano (Kilted, TensorRT fp16 detector, synthetic 1280x800 camera at 30 fps;
details in [docs/latex/measurements.pdf](latex/measurements.pdf) and the findings):

1. `jxb trace bench`: the detector's model takes 350 ms on the CPU, 35 ms with ONNX Runtime's CUDA EP and 10.8 ms
   as a TensorRT fp16 engine (F49). In the node it takes 17 ms with TensorRT fp16 instead of 46 ms with the CUDA EP,
   with 0.9 GB less RAM and 1 W less power (F50).
2. `jxb launch --monitor`: all 12 processes of the stack run natively and in the container; the image pipeline
   handles 24 of 30 frames/s (F59, F64).
3. `jxb trace nsys`: the image pipeline spends 2.2 ms per frame on the GPU but 14.6 ms in CUDA API calls, 9.1 ms
   of it in 16 device allocations and frees per frame (F64).
4. `jxb trace kernel`: that allocation churn shows up in the kernel too. nvmap's page-zeroing thread `nvmap-bz` uses
   39 % of a core. DDS moving the camera images over loopback costs 19 % of a core in network softirqs. The
   executor and DDS threads wait 0.1-1.3 ms (p99) for a CPU after a wakeup, on CPUs that are ~54 % busy (F80).
5. `jxb launch --mem-guard`, 25 minutes: memory grows for three minutes and then stays flat; no leak (F71).
6. `jxb launch --ros-trace` of the detector: its image callback runs 4.1 times per second (22 ms mean), while the
   camera publishes 25.8 images per second. Each image it takes is 0.32 s old. The node subscribes with the default
   QoS (reliable, keep last 10) and calls `spin_some()` at 5 Hz, so it always works on the oldest of the ten queued
   frames (F83). Keep-last-1 would hand it the newest frame.

So the image pipeline's per-frame buffer allocation is the first optimization target, not the detector's GPU time,
and the detector's queue makes its detections a third of a second late.

## Automation

- `summary.json` in every recording, `jxb trace report DIR --json` and `jxb trace list --json` are stable
  machine-readable results; recording commands take `--json` to print it.
- Commands exit non-zero on failure and never prompt (except sudo for kernel traces), so loops like
  `for m in a.onnx b.onnx; do jxb trace bench nano-lab --models $m --label $m; done` work as they are.
- CI (`.github/workflows/`) checks jxb itself; `board.yml` runs the sysroot and proof packages on a self-hosted
  runner with access to the boards.

## Pitfalls

| Symptom | Cause | What to do |
|---|---|---|
| The first run is slow or its numbers are off | TensorRT engines are built and caches filled | measure the second run |
| `rate` lower than the publisher's | the probe can't keep up with large messages (F70) | probe a small companion topic |
| tegrastats durations and energies 1.2-1.5 % off | its real interval is longer than `--interval` (F67) | from 30 s on, jxb fits the real interval from the samples' timestamps (`interval_real_ms`) and uses it for times and energy |
| Timestamps in 1970 in board logs | the board runs without NTP (F35) | jxb maps them to the PC's clock (F77) |
| Board reset during a run | a kernel bug in memory reclaim on the RT kernel (F79); it hit twice, once while the full stack started (F84) | keep memory pressure low (`--mem-guard`), start heavy stacks one at a time, tell the board's other users; the crash log is in pstore (root) |
| ROS callbacks named only by address | the processes started before the recording | start them inside it: `jxb launch --ros-trace` |
| Kernel trace shorter than `--duration` | ring buffer overflow | `--buffer-mb` as the report suggests |
| `fixed clocks` in the report | someone ran `jetson_clocks` | wait for the default clocks or compare only with runs at the same clocks |
