"""jxb launch: Renesas-RDK-style remote launch of a ROS 2 workspace on a board, natively or in the profile's runtime
container (--mode), optionally recording the session.

  jxb launch NAME [--ws DIR] [--packages P ...] [--no-deploy] [--duration S] [--monitor] [--bag [TOPIC ...]]
             [--with CMD ...] [--label L] [--json] -- TARGET [LAUNCH ARGS ...]

TARGET is `PKG FILE` (ros2 launch PKG FILE) or a launch file of the workspace such as launch/launch.py (deployed
with the workspace files, see `jxb deploy`). The workspace is deployed first (only --packages if given). Ctrl-C or
the end of --duration stops the launch with SIGINT, so the nodes shut down cleanly.

  --monitor   tegrastats and per-process CPU/RSS of the whole board (1 s), analyzed per node; waits for the
              board's measurement lock (~/jxb/.measure.lock) so measurements of several users don't overlap
  --bag       ros2 bag record of the given topics (all topics without a list), MCAP
  --with CMD  helper started next to the launch in the same environment and mode, stopped after it; {tools} is
              jxb's board tools, e.g. "python3 {tools}/jxb_image_pub.py --rate 30 /camera/color/image_raw"

The launch log, recordings and their analysis (`jxb trace report`, `jxb trace plot`) land in
traces/<board>/<stamp>-launch-<label>/.
"""
from __future__ import annotations

import shlex
import sys
import time
from pathlib import Path

import jxb_trace as T

J = None   # the jxb CLI module, set by register()
q = shlex.quote

# Board-wide per-process sampler: uptime + every process's /proc/<pid>/stat once a second (CPU ticks, RSS pages,
# parent). Runs on the board host, so it also sees the processes of a runtime container. "R" marks the session's
# shell and "C" the init of its container (JXB_CN): the analysis counts their descendants as the session's.
PROCMON = ('echo "R $$"; while :; do echo "T $(cut -d" " -f1 /proc/uptime)"; '
           'if [ -n "$JXB_CN" ] && [ -z "$JXB_CP" ]; then JXB_CP=$(docker inspect -f "{{.State.Pid}}" "$JXB_CN" '
           '2>/dev/null | grep -v "^0$") && echo "C $JXB_CP"; fi; '
           'cat /proc/[0-9]*/stat 2>/dev/null; sleep 1; done')


def _label(ws: Path, target: list[str]) -> str:
    """The launch file's name, plus module/platform arguments (SAM: launch.py module:=core -> launch-core)."""
    target = [t for t in target if t]
    if (ws / target[0]).is_file():
        name = Path(target[0]).stem
    else:
        name = Path(target[1]).stem if len(target) > 1 else target[0]
    picks = [t.split(":=", 1)[1] for t in target if ":=" in t and t.split(":=", 1)[0] in ("module", "platform")]
    return "-".join([name, *picks])


def launch_script(p, board, ws: Path, a, remote: str, tools: str) -> str:
    """The script that runs on the board (natively or in the runtime container): helpers, bag, the launch.

    Helpers, the recorder and the launch each run in their own session (setsid doesn't fork for a background job of
    a non-interactive shell, so $! is the process group): one signal stops a whole process tree, including nodes
    whose ros2 launch is gone (SIGKILL after the grace period, a lost connection without a terminal). The script
    forwards Ctrl-C and hangups to the launch's group and sweeps all groups at the end."""
    run = ["ros2", "launch", *J.board_launch_args(board, ws, a.cmd)]
    if a.duration:   # --foreground: no process group of its own, the launch stays in its session's group
        run = ["timeout", "--foreground", "-s", "INT", "-k", "20", str(a.duration), *run]
    parts = ["export PYTHONUNBUFFERED=1 RCUTILS_LOGGING_BUFFERED_STREAM=0; groups=; "]
    helpers = [w.replace("{tools}", tools) for w in a.with_ if w.strip()]   # empty: an unset VS Code setting
    for i, cmd in enumerate(helpers):
        parts.append(f"setsid bash -c {q(cmd)} > {q(f'{remote}/with{i}.log')} 2>&1 & groups=\"$groups $!\"; ")
    if a.bag is not None:   # topic names have no spaces: "t1 t2" (one argument, e.g. a VS Code setting) = t1 t2
        topics = [t for item in a.bag for t in item.split()]
        parts.append(f"setsid ros2 bag record -s mcap -o {q(remote + '/bag')} {shlex.join(topics or ['-a'])} "
                     f"> {q(remote + '/bag.log')} 2>&1 & JXB_BAG=$!; ")
    if helpers or a.bag is not None:
        parts.append("sleep 2; ")
    inner = f"{shlex.join(run)} 2>&1 | tee -i {q(remote + '/launch.log')}; exit ${{PIPESTATUS[0]}}"
    parts.append(f"setsid bash -c {q(inner)} & JXB_L=$!; echo $JXB_L > {q(remote + '/pgid')}; "
                 "trap 'kill -INT -- -$JXB_L 2>/dev/null' INT TERM HUP; "
                 "wait $JXB_L; rc=$?; while kill -0 $JXB_L 2>/dev/null; do wait $JXB_L; rc=$?; done; "
                 "trap - INT TERM HUP; ")
    # helpers, then the recorder: SIGTERM (background jobs of a non-interactive shell ignore SIGINT)
    parts.append("for g in $groups; do kill -TERM -- -$g 2>/dev/null; done; ")
    if a.bag is not None:   # numeric topics as CSV for the analysis (inference times, latencies)
        parts.append(f"kill -TERM -- -$JXB_BAG 2>/dev/null; wait $JXB_BAG; python3 {q(tools + '/jxb_bag_csv.py')} "
                     f"{q(remote + '/bag')} >> {q(remote + '/bag.log')} 2>&1; ")
    # leftovers: nodes of a killed launch, helper children that ignored SIGTERM
    parts.append(f"sleep 1; for g in $JXB_L $groups; do kill -KILL -- -$g 2>/dev/null; done; "
                 f"touch {q(remote + '/.done')}; exit $rc")
    return J.board_env_script(p, board, ws) + "".join(parts)


def cmd_launch(p, a) -> None:
    if not a.cmd:
        J.fail("usage: jxb launch NAME [options] -- PKG FILE [ARGS ...] | LAUNCH_FILE [ARGS ...]")
    board = J.Board(a.name, p)
    ws = Path(a.ws or Path.cwd()).resolve()
    if not a.no_deploy:
        J.deploy_workspace(p, board, ws, a.packages)
    local, remote = T._new_trace(board, "launch", a.label or _label(ws, a.cmd))
    tools = f"{board.base}/tools"
    board.ssh(f"mkdir -p {q(remote)} {q(tools)}")
    J.sh(["rsync", "-a", "-e", shlex.join(["ssh", *board.ssh_opts]), f"{J.REPO}/tools/board/",
          f"{board.target}:{tools}/"])
    tty = T._tty()
    container = J.uses_container(p, board)
    name = f"jxb-{local.name}" if container else None
    inner = J.board_exec(p, board, launch_script(p, board, ws, a, remote, tools), tty=tty, name=name)
    outer = inner
    if a.monitor:
        outer = (f"export JXB_CN={name or ''}; getconf CLK_TCK > {q(remote + '/sysconf')}; "
                 f"getconf PAGESIZE >> {q(remote + '/sysconf')}; "
                 f"( {PROCMON} ) < /dev/null > {q(remote + '/procstat.log')} 2> /dev/null & "
                 "JXB_PM=$!; "
                 + T._with_tegrastats(f"{remote}/tegrastats.log", a.interval,
                                      f"{inner}; JXB_RC=$?; kill $JXB_PM 2>/dev/null; (exit $JXB_RC)"))
    mode = "container" if container else "native"
    J.info(f"launch on {board.name} ({mode}): {shlex.join(a.cmd)}"
           + (f" for {a.duration} s" if a.duration else " (Ctrl-C stops)"))
    command = f"bash -c {q(outer)}"
    if a.monitor:   # measurements wait for each other on a shared board
        command = T.locked(board, f"jxb launch --monitor {local.name}", command, a)
    started, t0 = T._now(), time.time()
    try:
        rc = board.ssh(command, tty=tty, check=False)
    except KeyboardInterrupt:   # without a terminal Ctrl-C stays here: stop the launch on the board, keep the data
        J.info(f"stopping the launch on {board.name}")
        stop = f"kill -INT -- -$(cat {q(remote + '/pgid')}) 2>/dev/null"
        if name:   # the pid in the file belongs to the container's PID namespace
            stop = f"docker exec {name} sh -c {q(stop)}"
        board.ssh(f"{stop}; for i in $(seq 90); do [ -e {q(remote + '/.done')} ] && break; sleep 1; done; true",
                  check=False)
        rc = 130
    T._save(board, p, a, "launch", local, remote, started, time.time() - t0, rc, mode=mode, target=list(a.cmd),
            helpers=[w for w in a.with_ if w.strip()], bag=a.bag, interval_ms=a.interval if a.monitor else None)
    # a launch ended by --duration or Ctrl-C (SIGINT) is a normal end
    sys.exit(0 if rc in (0, 124, 130, -2) else rc)


def register(sub, jxb) -> None:
    global J
    J = jxb
    s = sub.add_parser("launch", help="deploy and ros2 launch on a board [-- TARGET ARGS ...]",
                       description=__doc__, formatter_class=jxb.argparse.RawDescriptionHelpFormatter)
    s.add_argument("name", help="board (boards.toml)")
    s.add_argument("--ws", help="ROS 2 workspace (default: current directory)")
    s.add_argument("--packages", nargs="+", metavar="PKG", help="deploy only these packages")
    s.add_argument("--no-deploy", action="store_true", help="launch what is deployed already")
    s.add_argument("--duration", type=int, help="stop the launch after S seconds")
    s.add_argument("--monitor", action="store_true", help="record tegrastats and per-process CPU/RSS")
    s.add_argument("--interval", type=int, default=500, help="tegrastats interval in ms (default 500)")
    s.add_argument("--bag", nargs="*", metavar="TOPIC", help="record these topics (no list: all)")
    s.add_argument("--with", dest="with_", action="append", default=[], metavar="CMD",
                   help="helper command next to the launch (repeatable; {tools} = jxb's board tools)")
    s.add_argument("--label", help="suffix of the trace directory (default: the launch file's name)")
    s.add_argument("--keep", action="store_true", help="keep the recording on the board")
    s.add_argument("--no-lock", action="store_true", help="--monitor without the board's measurement lock")
    s.add_argument("--json", action="store_true", help="print summary.json instead of the text report")
    s.set_defaults(fn=cmd_launch, cmd=[])
