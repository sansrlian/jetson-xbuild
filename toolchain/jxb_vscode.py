"""jxb vscode init: VS Code tasks and debug configurations for a ROS 2 workspace on Jetson boards.

  jxb vscode init [-w WS] [--board NAME] [--pkg P --exe E] [--launch PKG FILE] [--into-workspace] [--out FILE]

The workflows of the Renesas RDK's VS Code setup (build, deploy, run or launch on the target, gdbserver + cppdbg
debugging of a node or of one node of a launch file, SSH to the target) plus jxb's tracing, all through jxb:
keys-only SSH, gdbserver bound to the board's loopback and reached through the SSH session.

By default this writes a multi-root workspace (the ROS 2 workspace + this repo) to vscode/<ws>.code-workspace, so
the workspace's own .vscode/ stays untouched; open it with `code vscode/<ws>.code-workspace`. Running init again
regenerates tasks and launch configurations and keeps the settings. --into-workspace writes
<ws>/.vscode/{tasks,launch,settings}.json instead, only where none of them exists yet.

What the tasks act on comes from settings, like the RDK's settings.json variables (edit them in the workspace file
or in VS Code's settings UI): jxb.profile, jxb.mode (auto | native | container), jxb.board, jxb.package,
jxb.executable, jxb.launchPackage + jxb.launchFile (or an empty package and a launch file of the workspace such
as launch/launch.py), jxb.launchArgs, jxb.launchWith (helper next to the launch, e.g. a synthetic camera),
jxb.bagTopics, jxb.benchModels, jxb.gdbPort, jxb.traceSeconds.

Several --board options debug on several boards at once: the first is jxb.board, every further one gets its own
gdbserver task and debug configuration (with its boards.toml gdb_port), and the compound "jxb: debug node on all
boards" starts them together.
"""
from __future__ import annotations

import json
from pathlib import Path

J = None   # the jxb CLI module, set by register()


def _cfg(key: str) -> str:
    return "${config:jxb.%s}" % key


def generate(wsf: str, others: list[tuple[str, int]] = ()) -> tuple[dict, dict]:
    """tasks.json and launch.json content; wsf is how VS Code names the ROS 2 workspace folder, others are further
    boards (name, gdb port) to debug on at the same time."""
    jxb = str(J.REPO / "jxb")
    P, B, PKG, EXE = _cfg("profile"), _cfg("board"), _cfg("package"), _cfg("executable")
    LP, LF, SEC, MODE = _cfg("launchPackage"), _cfg("launchFile"), _cfg("traceSeconds"), _cfg("mode")
    ros_run, ros_launch = ["ros2", "run", PKG, EXE], ["ros2", "launch", LP, LF]
    # launchPackage may be empty: launchFile is then a launch file of the workspace (e.g. launch/launch.py)
    target = f"{LP} {LF} {_cfg('launchArgs')}"

    def task(label: str, args: list[str], detail: str, cwd: str = wsf, **extra) -> dict:
        return {"label": label, "detail": detail, "type": "process", "command": jxb,
                "args": ["--profile", P, "--mode", MODE, *args], "options": {"cwd": cwd}, "problemMatcher": [],
                "presentation": {"panel": "dedicated", "clear": True}, **extra}

    def shell(label: str, cmdline: str, detail: str) -> dict:
        # a shell task: settings holding several words (launch arguments, topics, models) split into arguments
        return {"label": label, "detail": detail, "type": "shell",
                "command": f"'{jxb}' --profile {P} --mode {MODE} {cmdline}", "options": {"cwd": wsf},
                "problemMatcher": [], "presentation": {"panel": "dedicated", "clear": True}}

    def gdbserver(label: str, extra: list[str], detail: str, board: str = B) -> dict:
        # background task: VS Code starts the debugger once gdbserver listens (forwarded to 127.0.0.1:gdbPort)
        return task(label, ["debug", board, "--ws", wsf, "--pkg", PKG, "--exe", EXE, *extra, "--server-only"], detail,
                    isBackground=True,
                    problemMatcher={"owner": "jxb", "pattern": {"regexp": "^__jxb_never_matches__$"},
                                    "background": {"activeBegin": True, "beginsPattern": "Listening on port",
                                                   "endsPattern": "Listening on port"}})

    def sequence(label: str, *steps: str) -> dict:
        return {"label": label, "dependsOn": list(steps), "dependsOrder": "sequence", "problemMatcher": []}

    tasks = [
        task("jxb: build", ["build"], "cross-build the workspace (Release) for jxb.profile",
             group={"kind": "build", "isDefault": True}, problemMatcher=["$gcc"]),
        task("jxb: build package (debug info)",
             ["build", "--packages-up-to", PKG, "--cmake-args", "-DCMAKE_BUILD_TYPE=RelWithDebInfo"],
             "cross-build jxb.package and its dependencies with debug info", group="build", problemMatcher=["$gcc"]),
        task("jxb: deploy", ["deploy", B, "--ws", wsf], "rsync install-<profile>/ and jxb's runtime to jxb.board"),
        sequence("jxb: build + deploy", "jxb: build", "jxb: deploy"),
        sequence("jxb: build package (debug info) + deploy", "jxb: build package (debug info)", "jxb: deploy"),
        task("jxb: run node on board", ["run", B, "--ws", wsf, "--", *ros_run],
             "ros2 run jxb.package jxb.executable on jxb.board"),
        shell("jxb: launch on board", f"launch {B} --ws '{wsf}' -- {target}",
              "deploy, then ros2 launch jxb.launchPackage jxb.launchFile jxb.launchArgs on jxb.board (Ctrl-C stops)"),
        shell("jxb: launch + monitor (recorded)",
              f"launch {B} --ws '{wsf}' --monitor --duration {SEC} --with \"{_cfg('launchWith')}\" -- {target}",
              "the launch for jxb.traceSeconds with per-node CPU/RAM and tegrastats (+ jxb.launchWith helper)"),
        shell("jxb: launch + monitor + bag (recorded)",
              f"launch {B} --ws '{wsf}' --monitor --duration {SEC} --with \"{_cfg('launchWith')}\" "
              f"--bag {_cfg('bagTopics')} -- {target}",
              "as above plus ros2 bag record of jxb.bagTopics (empty: all topics)"),
        gdbserver("jxb: gdbserver node", [], "jxb.executable under gdbserver (preLaunchTask)"),
        gdbserver("jxb: cuda-gdbserver node", ["--cuda"],
                  "jxb.executable under cuda-gdbserver, for CUDA kernels too (preLaunchTask)"),
        *(gdbserver(f"jxb: gdbserver node ({name})", ["--port", str(port)],
                    f"jxb.executable under gdbserver on {name} (preLaunchTask)", board=name)
          for name, port in others),
        {**shell("jxb: gdbserver node in launch",
                 f"debug {B} --ws '{wsf}' --pkg {PKG} --exe {EXE} --launch \"{LP}\" {LF} --server-only "
                 f"-- {_cfg('launchArgs')}",
                 "the launch file runs (with jxb.launchArgs), jxb.executable under gdbserver (preLaunchTask)"),
         "isBackground": True,
         "problemMatcher": {"owner": "jxb", "pattern": {"regexp": "^__jxb_never_matches__$"},
                            "background": {"activeBegin": True, "beginsPattern": "Listening on port",
                                           "endsPattern": "Listening on port"}}},
        {"label": "jxb: stop gdbserver launch", "detail": "ends the launch file after debugging (postDebugTask)",
         "type": "shell", "command": "echo ${input:jxbStopLaunch}", "problemMatcher": [],
         "presentation": {"reveal": "never"}},
        task("jxb: ssh to board", ["ssh", B, "--ws", wsf], "shell on jxb.board with ROS and the deployment sourced"),
        task("jxb: board check", ["board", "check", B], "compare jxb.board's packages with the sysroot"),
        task("jxb: test suite on board", ["test", "--board", B], "cross-build jxb's tests and run them on jxb.board",
             cwd=str(J.REPO)),
        task("jxb: trace nsys node", ["trace", "nsys", B, "--ws", wsf, "--duration", SEC, "--tegrastats",
                                      "--label", EXE, "--", *ros_run],
             "Nsight Systems + tegrastats of jxb.executable for jxb.traceSeconds"),
        shell("jxb: trace nsys launch",
              f"trace nsys {B} --ws '{wsf}' --duration {SEC} --tegrastats -- ros2 launch {target}",
              "Nsight Systems + tegrastats of the launch file (with jxb.launchArgs) for jxb.traceSeconds"),
        task("jxb: tegrastats live", ["trace", "tegrastats", B, "--live"], "CPU/GPU/RAM/power of jxb.board, live"),
        task("jxb: kernel trace", ["trace", "kernel", B, "--duration", SEC, "--tegrastats"],
             "ftrace sched/irq/power for jxb.traceSeconds (sudo asks for your password in the terminal)"),
        task("jxb: kernel trace live (top)", ["trace", "kernel", B, "--live", "--top"],
             "per-second scheduler statistics from ftrace (sudo asks for your password)"),
        task("jxb: trace report (latest)", ["trace", "report", B], "analysis of jxb.board's latest recording"),
        task("jxb: trace plot (latest)", ["trace", "plot", B], "plots + report.md of jxb.board's latest recording"),
        shell("jxb: benchmark models", f"trace bench {B} --models {_cfg('benchModels')}",
              "ONNX Runtime CPU/CUDA/TensorRT EP vs TensorRT engines for the ONNX files in jxb.benchModels"),
        task("jxb: container shell", ["shell"], "shell in the cross-build container of jxb.profile"),
    ]
    tasks_json = {"version": "2.0.0", "tasks": tasks,
                  "inputs": [{"id": "jxbStopLaunch", "type": "command", "command": "workbench.action.tasks.terminate",
                              "args": "jxb: gdbserver node in launch"}]}

    def debug(name: str, pre: str, post: str | None = None, cuda: bool = False, port: int | None = None) -> dict:
        c = {"name": name, "type": "cppdbg", "request": "launch",
             "program": f"{wsf}/install-{P}/{PKG}/lib/{PKG}/{EXE}", "args": [], "cwd": wsf,
             "MIMode": "gdb", "targetArchitecture": "arm64",
             # jxb-gdb sets the profile's sysroot and the workspace's library paths (--jxb-cuda: cuda-gdb)
             "miDebuggerPath": str(J.REPO / "toolchain/bin/jxb-gdb"),
             "miDebuggerArgs": f"--jxb-profile={P} --jxb-ws={wsf}" + (" --jxb-cuda" if cuda else ""),
             "miDebuggerServerAddress": f"127.0.0.1:{port or _cfg('gdbPort')}",
             "stopAtEntry": False, "externalConsole": False,
             "setupCommands": [{"description": "pretty printing", "text": "-enable-pretty-printing",
                                "ignoreFailures": True}],
             "preLaunchTask": pre}
        if post:
            c["postDebugTask"] = post
        return c

    launch_json = {"version": "0.2.0", "configurations": [
        debug("jxb: debug node on board", "jxb: gdbserver node"),
        debug("jxb: debug node on board (CUDA)", "jxb: cuda-gdbserver node", cuda=True),
        debug("jxb: debug node in launch file on board", "jxb: gdbserver node in launch",
              "jxb: stop gdbserver launch"),
        *(debug(f"jxb: debug node on {name}", f"jxb: gdbserver node ({name})", port=port) for name, port in others)]}
    if others:
        launch_json["compounds"] = [{"name": "jxb: debug node on all boards",
                                     "configurations": ["jxb: debug node on board",
                                                        *(f"jxb: debug node on {name}" for name, _ in others)]}]
    return tasks_json, launch_json


def cmd_init(p, a) -> None:
    ws = Path(a.workspace or Path.cwd()).resolve()
    if not (ws / "src").is_dir():
        J.fail(f"{ws} has no src/: not a ROS 2 workspace")
    inv = J.load_toml(J.REPO / "boards.toml")
    boards = inv.get("boards", {})
    board = (a.board or [None])[0] or p.local.get("board") or next(iter(boards), "")
    for b in [board, *(a.board or [])[1:]]:
        if b and b not in boards:
            J.fail(f"unknown board {b}; known: {', '.join(boards) or '(none, see boards.example.toml)'}")

    def port_of(b: str) -> int:
        return int({**inv.get("defaults", {}), **boards.get(b, {})}.get("gdb_port", 3000))
    port = port_of(board)
    others = [(b, port_of(b)) for b in dict.fromkeys((a.board or [])[1:]) if b != board]
    ports = [port, *(p_ for _, p_ in others)]
    if len(set(ports)) < len(ports):   # each session forwards its board's gdbserver to 127.0.0.1:<port> here
        J.fail(f"boards {', '.join([board, *(b for b, _ in others)])} need different gdb_port values in boards.toml "
               f"to be debugged at once (now: {', '.join(map(str, ports))})")
    given = {"jxb.board": (a.board or [None])[0], "jxb.package": a.pkg, "jxb.executable": a.exe,
             "jxb.launchPackage": a.launch[0] if a.launch else None,
             "jxb.launchFile": a.launch[1] if a.launch else None}
    defaults = {"jxb.profile": p.name, "jxb.mode": "auto", "jxb.board": board, "jxb.package": "",
                "jxb.executable": "", "jxb.launchPackage": "", "jxb.launchFile": "", "jxb.launchArgs": "",
                "jxb.launchWith": "", "jxb.bagTopics": "", "jxb.benchModels": "", "jxb.gdbPort": port,
                "jxb.traceSeconds": 10}

    if a.into_workspace:
        tasks, launch = generate("${workspaceFolder}", others)
        settings = {**defaults, **{k: v for k, v in given.items() if v is not None}}
        files = {ws / ".vscode/tasks.json": tasks, ws / ".vscode/launch.json": launch,
                 ws / ".vscode/settings.json": settings}
        existing = [str(f) for f in files if f.exists()]
        if existing:
            J.fail(f"not overwriting {', '.join(existing)}; run without --into-workspace for a separate "
                   "workspace file")
        (ws / ".vscode").mkdir(exist_ok=True)
        for f, content in files.items():
            f.write_text(json.dumps(content, indent=4, ensure_ascii=False) + "\n")
        J.info(f"wrote {ws}/.vscode/{{tasks,launch,settings}}.json")
        return

    out = Path(a.out).resolve() if a.out else J.REPO / "vscode" / f"{ws.name}.code-workspace"
    old: dict = {}
    if out.exists():
        try:
            old = json.loads(out.read_text())
        except json.JSONDecodeError:
            if not a.force:
                J.fail(f"{out} is not plain JSON (comments?); --force regenerates it and drops your settings")
    # Generated parts are replaced; settings keep what was edited, except values given on the command line.
    settings = {**defaults, **old.get("settings", {}), **{k: v for k, v in given.items() if v is not None}}
    tasks, launch = generate(f"${{workspaceFolder:{ws.name}}}", others)
    doc = {"folders": [{"path": str(ws), "name": ws.name}, {"path": str(J.REPO), "name": "jxb"}],
           "settings": settings, "tasks": tasks, "launch": launch,
           "extensions": {"recommendations": ["ms-vscode.cpptools"]}}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=4, ensure_ascii=False) + "\n")
    J.info(f"{'updated' if old else 'wrote'} {out}")
    print(f"  open it:  code {out}")
    missing = [k for k in ("jxb.board", "jxb.package", "jxb.executable") if not settings.get(k)]
    if missing:
        print(f"  then set {', '.join(missing)} in its settings (or: jxb vscode init --pkg P --exe E)")
    print("  Run Task: jxb: build / deploy / run node / launch / trace ...; Run and Debug: jxb: debug node on board")


def register(sub, jxb) -> None:
    global J
    J = jxb
    s = sub.add_parser("vscode", help="generate VS Code tasks and debug configurations",
                       description=__doc__, formatter_class=jxb.argparse.RawDescriptionHelpFormatter)
    vs = s.add_subparsers(dest="vscode_cmd", required=True)
    i = vs.add_parser("init", help="workspace file with jxb tasks and launch configurations")
    i.add_argument("-w", "--workspace", help="ROS 2 workspace (default: current directory)")
    i.add_argument("--board", action="append", help="default board (setting jxb.board); repeat it for debug "
                   "configurations on several boards at once")
    i.add_argument("--pkg", help="package to run/debug (setting jxb.package)")
    i.add_argument("--exe", help="executable to run/debug (setting jxb.executable)")
    i.add_argument("--launch", nargs=2, metavar=("PKG", "FILE"), help="launch file (jxb.launchPackage/File)")
    i.add_argument("--into-workspace", action="store_true", help="write <ws>/.vscode/*.json (never overwrites)")
    i.add_argument("--out", help="workspace file to write (default: vscode/<ws>.code-workspace)")
    i.add_argument("--force", action="store_true", help="regenerate a workspace file that is not plain JSON")
    i.set_defaults(fn=cmd_init)
