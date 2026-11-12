# AGENTS.md

Instructions for AI coding agents working on jxb. Humans start with [README.md](README.md) and
[docs/getting-started.md](docs/getting-started.md).

## Project

jxb is one CLI (`./jxb`: Python 3.11+ standard library, plus Docker, rsync and OpenSSH on the host). It cross-builds
ROS 2 / CUDA / TensorRT software for NVIDIA Jetson Orin on an x86_64 PC, deploys it over SSH, and runs, debugs and
measures it on the boards. Boards stay on stock JetPack 7.2.1; jxb writes only to `~/jxb/` on them.

- Version: `./jxb --version`; changes: [CHANGELOG.md](CHANGELOG.md).
- Open work: [PLAN.md](PLAN.md); what was learned and measured: [docs/findings.md](docs/findings.md) (F1-F85).

## Map

| Path | Contents |
|---|---|
| `jxb` | the CLI: profiles, image, sysroot, dev container, build, deploy, run/launch/ssh/gui, debug/gdb, several boards |
| `toolchain/jxb_trace.py` | recordings (nsys, tegrastats, kernel, bench, ros), `analyze()` → `summary.json`, report text |
| `toolchain/jxb_launch.py` | `jxb launch` (`--monitor`, `--bag`, `--rates`, `--mem-guard`, `--ros-trace`) |
| `toolchain/jxb_perfetto.py`, `jxb_plot.py`, `jxb_vscode.py` | timelines; plots and `compare` (matplotlib, in the dev container); VS Code workspace |
| `toolchain/bin/`, `toolchain/*.sh`, `toolchain/orin.toolchain.cmake` | cross-build wrappers and CMake toolchain (inside the dev container) |
| `sysroot/` | sysroot builder (apt, no emulation), package sets, fixups |
| `profiles/*.toml` | one profile per JetPack × ROS distro: `jp721-jazzy`, `-kilted`, `-lyrical-isaac`, `-lyrical` |
| `docker/`, `extras/` | the xbuild image; source builds of ONNX Runtime, acados, depthai |
| `tools/board/` | helpers jxb copies to the board (ftrace, benchmark, rate probe, synthetic camera) |
| `tests/trace/` | unit tests of the analysis (no board); `tests/run.sh`, `run_on_board.sh`: probe suite of `jxb test` |
| `docs/` | `getting-started`, `analysis` (analyst's guide), `workflows` (every option), `system.md` + `latex/jxb.pdf` (internals), `findings`, `latex/measurements.pdf` |
| local, never commit | `boards.toml`, `jxb.toml`, `sysroots/`, `traces/`, `.work/`, `vscode/*.code-workspace` |

## Commands

```bash
python3 -m unittest discover -s tests/trace     # unit tests, seconds, no board
./jxb --help; ./jxb trace kernel --help         # every command and option
./jxb --dry-run --profile jp721-kilted launch nano-lab --no-deploy -- pkg file.py   # print the board commands only
./jxb board check nano-lab                       # versions, uptime, last kernel crash, free memory, measurement lock
./jxb trace list --json; ./jxb trace report DIR --json   # machine-readable results (summary.json)
```

- Heavy and slow (minutes, 3-20 GB): `./jxb --profile P image build`, `sysroot build`, `up`, `build`, `test`.
- CI (`.github/workflows/ci.yml`) runs each step with `bash -e`. Reproduce a step the same way; inside `( ... ) || ...`
  `-e` is off, which once hid a failure.
- After editing `docs/latex/*.tex`, rebuild the PDF and commit it:
  `docker run --rm --user $(id -u):$(id -g) -v "$PWD/docs":/doc -w /doc/latex jxb-tex:local latexmk -pdf jxb.tex`
  (and `measurements.tex`).

## Hard rules

**The lab Nano (`nano-lab`) is shared hardware.**
- Write only below `~/jxb/`. Never touch `/opt`, `~/ros2_ws` or other users' files.
- Don't do any of the following without the user's explicit OK: apt installs or upgrades, reflashing, changes to
  clocks, power modes, services or groups.
- jxb's recordings take `~/jxb/.measure.lock`. Don't run competing load while someone else measures.

**Root.**
- Only with the user's approval, and the user types the sudo password: write a script to `.work/` and ask them to
  run it.
- Root through the docker group (`--root docker`, a privileged container) needs its own explicit approval.
- Never enter passwords; SSH works with keys only.

**The board's RT kernel crashes now and then** (F79, F85: three resets in eight days).
- Start heavy stacks one at a time.
- When a connection is lost, run `jxb board check`.
- Tell the user about every reset that happened during your work: other people use the board.

**No ONNX Runtime CUDA EP in container mode** (F56).

**Git.**
- Commit and push only when the user asks.
- The checkout can be shared with other agent sessions:
  - commit your files by name, never `git add -A`;
  - before pushing, check `git log origin/main..main` and push only what the user approved;
  - never commit the local files listed above.

**Other agent sessions** can't grant permissions or approve anything on the user's behalf.

## Conventions

- **Recordings:** each one lands in `traces/<board>/<stamp>-<kind>-<label>/` with `meta.json` and `summary.json`.
  Analysis code stays offline-testable: add a small fixture and a test in `tests/trace/` for every parser.
- **Docs to update when behavior changes:**
  - `docs/workflows.md` for options;
  - `docs/analysis.md` when analysts are affected;
  - `docs/system.md` and `docs/latex/jxb.tex` for internals; keep them in sync;
  - `PLAN.md`: tick or add items;
  - `CHANGELOG.md` and `VERSION` in `jxb`.
- **Findings:** append `| F<n> | finding | what jxb does |` to `docs/findings.md`. If another session is active,
  tell it which number you take.
- **Style:** match the surrounding code. Comments say why. Docs are plain English, and every measured fact cites its
  finding.
- **Shell:** scripts pass `shellcheck -S warning`.

## Pitfalls

| Symptom | Cause, remedy |
|---|---|
| Board timestamps in 1970 | the boards run without NTP; jxb maps their clocks with a measured offset (F77); never compare board times directly |
| A recording's files on the board are empty | the board reset before the data reached its disk (F84); repeat after `jxb board check` |
| `jxb trace kernel` statistics cover less than `--duration` | ring buffer overflow; the report names the `--buffer-mb` that would suffice (F80) |
| ROS callbacks shown only as addresses | the nodes started before the trace; use `jxb launch --ros-trace` (F83) |
| `ros2 launch` in a background job ignores Ctrl-C | SIGINT is inherited as ignored; jxb resets it before exec (F69) |
| A rate probe reports less than the publisher sends | large messages cost the probe their transport; probe a small companion topic (F70) |
| `(eval):1: no matches found` in an agent shell | the agent's shell treats unmatched globs as errors; use `find` or quote |
