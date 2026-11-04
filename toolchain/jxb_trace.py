"""jxb trace: profiling and tracing on the board, results and analysis on the host.

  jxb trace nsys NAME [--duration S] [--trace LIST] [--tegrastats] -- CMD ...
      Nsight Systems: CUDA, NVTX, OS runtime, cuBLAS/cuDNN, CPU sampling of the process tree (no root needed)
  jxb trace tegrastats NAME [--interval MS] [--duration S] [--live] [-- CMD ...]
      CPU/GPU/EMC load and clocks, RAM, temperatures, power rails (no root needed)
  jxb trace kernel NAME [--events sched,irq,power] [--duration S] [--live [--top]] [--root sudo|docker|none]
      [-- CMD ...]   ftrace via tracefs (scheduler, IRQs, CPU frequency/idle). Needs root on the board: by
      default sudo asks for your password in the terminal (jxb never handles passwords)
  jxb trace report [DIR|BOARD|latest] [--json]      analysis of a recording (writes summary.json)
  jxb trace list [--json]
  jxb trace gui NAME [--tool nsys|ncu]               the board's Nsight GUI as a window here (X11 over SSH)

Recordings land in traces/<board>/<stamp>-<kind>[-<label>]/ with meta.json; stamps come from the host clock
(boards may run without NTP). summary.json is the machine-readable result (e.g. for JetsonPlanner). Nothing is
installed on the board: nsys and tegrastats come with JetPack, kernel traces use tracefs and a small helper
script that is copied next to the recordings (tools/board/jxb-ftrace.sh).
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import os
import re
import shlex
import sqlite3
import statistics
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

J = None   # the jxb CLI module (Board, board_env_script, info, fail, sh, REPO), set by register()
q = shlex.quote

NSYS_TRACE = "cuda,nvtx,osrt,cublas,cudnn"
KERNEL_EVENTS = {
    "sched": ["sched/sched_switch", "sched/sched_wakeup", "sched/sched_wakeup_new", "sched/sched_migrate_task",
              "sched/sched_process_exec", "sched/sched_process_exit"],
    "irq": ["irq/irq_handler_entry", "irq/irq_handler_exit", "irq/softirq_entry", "irq/softirq_exit"],
    "power": ["power/cpu_frequency", "power/cpu_idle"],
    "syscalls": ["raw_syscalls/sys_enter", "raw_syscalls/sys_exit"],
    "workqueue": ["workqueue/workqueue_execute_start", "workqueue/workqueue_execute_end"],
}
# jxb's own options of the trace subcommands that take a value (the rest are flags); everything after "--" is CMD
VALUE_OPTIONS = {"--ws", "--label", "--duration", "--delay", "--trace", "--sample", "--interval", "--nsys-arg",
                 "--events", "--root"}


# ----------------------------------------------------------------------------------------------- helpers
def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _tty() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _trace_root(board) -> str:
    # below the profile's base: the only writable mount of the runtime container
    return board.cfg.get("trace_dir", f"{board.base}/traces")


def _on_board(p, board, ws: Path, command: str, remote: str, tty: bool, tegrastats: bool = False,
              interval: int = 500) -> str:
    """Board-host script: creates the trace dir and runs command with the deployment's environment, natively or in
    the profile's runtime container (board_exec); tegrastats (a board-host tool) runs around it."""
    inner = J.board_exec(p, board, J.board_env_script(p, board, ws) + command, tty=tty)
    if tegrastats:
        inner = _with_tegrastats(f"{remote}/tegrastats.log", interval, inner)
    return f"bash -c {q(f'mkdir -p {q(remote)}; {inner}')}"


def locked(board, desc: str, command: str, a=None, timeout: int = 3600) -> str:
    """Wraps a board-host command in the board's measurement lock (~/jxb/.measure.lock, flock): measurements of
    several users or sessions on a shared board run one after another instead of measuring each other's load.
    --no-lock (a.no_lock) skips it."""
    if a is not None and getattr(a, "no_lock", False):
        return command
    lock = board.cfg.get("measure_lock", f"/home/{board.user}/jxb/.measure.lock")
    note = f"{desc}, since {dt.datetime.now():%Y-%m-%d %H:%M:%S} (host time)"
    # .info names the holder while the lock is held (holders that don't write it show as unknown)
    held = f"echo {q(note)} > {q(lock)}.info; {command}; rc=$?; rm -f {q(lock)}.info; exit $rc"
    return (f"flock -n {q(lock)} true 2>/dev/null || echo \"[jxb] {board.name} is busy with another measurement "
            f"($(cat {q(lock)}.info 2>/dev/null || echo 'holder unknown')); waiting up to {timeout} s\"; "
            f"exec flock -o -w {timeout} -E 75 {q(lock)} bash -c {q(held)}")


def _nsys(p, board) -> str:
    """nsys for the profiled command: on PATH natively; in the runtime container only /opt/nvidia/nsight-systems is
    mounted, so the real binary behind the board's nsys."""
    if "nsys" in board.cfg or not J.uses_container(p, board):
        return board.cfg.get("nsys", "nsys")
    return str(board.ssh('readlink -f "$(command -v nsys)"', capture=True)).strip() or "nsys"


def _new_trace(board, kind: str, label: str | None) -> tuple[Path, str]:
    name = dt.datetime.now().strftime("%Y%m%d-%H%M%S") + f"-{kind}"
    if label:
        name += "-" + re.sub(r"[^A-Za-z0-9_.-]+", "_", label)
    return J.REPO / "traces" / board.name / name, f"{_trace_root(board)}/{name}"


def _fetch(board, remote: str, local: Path) -> None:
    if not J.DRY_RUN:
        local.mkdir(parents=True, exist_ok=True)
    J.sh(["rsync", "-az", "-e", shlex.join(["ssh", *board.ssh_opts]), f"{board.target}:{remote}/", f"{local}/"])


def _with_tegrastats(log: str, interval: int, command: str) -> str:
    """Runs tegrastats in the background around a shell command, keeping the command's exit code. tegrastats gets
    no inherited stdio (a leftover must never hold the SSH channel open) and is stopped on any exit of the shell."""
    return (f"tegrastats --interval {interval} --logfile {q(log)} < /dev/null > /dev/null 2>&1 & JXB_TS=$!; "
            f"trap 'kill $JXB_TS 2>/dev/null' EXIT; trap 'exit 130' INT TERM HUP; {command}; rc=$?; "
            f"kill $JXB_TS 2>/dev/null; wait $JXB_TS 2>/dev/null; exit $rc")


def _save(board, p, a, kind: str, local: Path, remote: str, started: str, wall: float, rc: int, **extra) -> None:
    _fetch(board, remote, local)
    if J.DRY_RUN:
        return
    if not a.keep:
        board.ssh(f"rm -rf {q(remote)}", check=False)
    meta = {"kind": kind, "board": board.name, "host": board.host, "profile": p.name, "ros_distro": p.distro,
            "label": a.label, "command": list(a.cmd), "started": started, "wall_s": round(wall, 3), "rc": rc,
            **extra}
    (local / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    summary = analyze(local)
    print(json.dumps(summary, indent=2) if a.json else format_summary(summary))
    J.info(f"trace saved: {local}")


# ------------------------------------------------------------------------------------------------- nsys
def cmd_nsys(p, a) -> None:
    if not a.cmd:
        J.fail("usage: jxb trace nsys NAME [options] -- CMD ...")
    board = J.Board(a.name, p)
    ws = Path(a.ws or Path.cwd()).resolve()
    local, remote = _new_trace(board, "nsys", a.label)
    nsys = board.cfg.get("nsys", "nsys")   # the board host's, for the export
    rep = f"{remote}/report"
    prof = [_nsys(p, board), "profile", "--output", rep, "--force-overwrite", "true", "--trace", a.trace,
            "--sample", a.sample, "--cpuctxsw", a.sample, "--kill", "2"]   # SIGINT: ROS nodes shut down cleanly
    if a.duration:
        prof += ["--duration", str(a.duration)]
    if a.delay:
        prof += ["--delay", str(a.delay)]
    prof += [x for arg in a.nsys_arg for x in shlex.split(arg)]
    cmd = J.board_launch_args(board, ws, a.cmd)   # workspace launch files -> their deployed copies
    script = _on_board(p, board, ws, shlex.join(prof + cmd), remote, _tty(), a.tegrastats, a.interval)
    J.info(f"nsys on {board.name}: {shlex.join(a.cmd)} (Ctrl-C ends the recording and keeps the report)")
    started, t0 = _now(), time.time()
    rc = board.ssh(locked(board, f"jxb trace nsys {local.name}", script, a), tty=_tty(), check=False)
    wall = time.time() - t0
    J.info("exporting the report to SQLite on the board")
    board.ssh(f"out=$({nsys} export --type sqlite --force-overwrite true --output {q(rep + '.sqlite')} "
              f"{q(rep + '.nsys-rep')} 2>&1) || echo \"$out\" | tail -5", check=False)   # progress bars only on errors
    _save(board, p, a, "nsys", local, remote, started, wall, rc, trace=a.trace, sample=a.sample,
          interval_ms=a.interval if a.tegrastats else None)


def _union_ns(rows) -> int:
    total, start, end = 0, None, None
    for s, e in rows:
        if end is None or s > end:
            if end is not None:
                total += end - start
            start, end = s, e
        elif e > end:
            end = e
    return total + (end - start if end is not None else 0)


def nsys_summary(db: Path, top: int = 12) -> dict:
    """Kernel/memcpy/API/NVTX/OS-runtime/CPU-sample statistics from an `nsys export --type sqlite` database."""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")}

    def cols(t: str) -> set[str]:
        return {r[1] for r in con.execute(f"PRAGMA table_info({t})")}

    def grouped(sql: str) -> list[dict]:
        return [{"name": n, "count": c, "total_ms": round(tot / 1e6, 3), "avg_us": round(tot / c / 1e3, 2),
                 "max_us": round(mx / 1e3, 2)} for n, c, tot, mx in con.execute(sql)]

    out: dict = {"errors": []}
    spans = [con.execute(f"SELECT min(start), max(end) FROM {t}").fetchone()
             for t in ("CUPTI_ACTIVITY_KIND_KERNEL", "CUPTI_ACTIVITY_KIND_RUNTIME", "OSRT_API", "NVTX_EVENTS")
             if t in tables]
    spans = [s for s in spans if s[0] is not None and s[1] is not None]
    t0, t1 = (min(s[0] for s in spans), max(s[1] for s in spans)) if spans else (0, 0)
    out["span_s"] = round((t1 - t0) / 1e9, 3)

    def section(key: str, fn) -> None:
        try:
            out[key] = fn()
        except sqlite3.Error as e:   # schema differences between nsys versions: keep the other sections
            out["errors"].append(f"{key}: {e}")

    K = "CUPTI_ACTIVITY_KIND_KERNEL"
    if K in tables:
        def gpu() -> dict:
            name = "shortName" if "shortName" in cols(K) else "demangledName"
            ks = grouped(f"SELECT s.value, count(*), sum(k.end - k.start), max(k.end - k.start) FROM {K} k "
                         f"JOIN StringIds s ON s.id = k.{name} GROUP BY k.{name} ORDER BY 3 DESC")
            total = sum(k["total_ms"] for k in ks)
            for k in ks:
                k["pct"] = round(100 * k["total_ms"] / total, 1) if total else 0.0
            busy = _union_ns(con.execute(f"SELECT start, end FROM {K} ORDER BY start"))
            return {"kernel_launches": sum(k["count"] for k in ks), "kernel_time_ms": round(total, 3),
                    "busy_ms": round(busy / 1e6, 3),
                    "busy_pct": round(100 * busy / (t1 - t0), 1) if t1 > t0 else None, "top_kernels": ks[:top]}
        section("gpu", gpu)
    M = "CUPTI_ACTIVITY_KIND_MEMCPY"
    if M in tables:
        kinds = {1: "HtoD", 2: "DtoH", 8: "DtoD", 9: "HtoH", 10: "PtoP"}   # CUpti_ActivityMemcpyKind
        section("memcpy", lambda: [
            {"kind": kinds.get(k, str(k)), "count": c, "mb": round(b / 1e6, 3), "total_ms": round(t / 1e6, 3)}
            for k, c, b, t in con.execute(f"SELECT copyKind, count(*), sum(bytes), sum(end - start) FROM {M} "
                                          "GROUP BY copyKind ORDER BY 4 DESC")])
    R = "CUPTI_ACTIVITY_KIND_RUNTIME"
    if R in tables:
        section("cuda_api", lambda: grouped(
            f"SELECT s.value, count(*), sum(r.end - r.start), max(r.end - r.start) FROM {R} r "
            f"JOIN StringIds s ON s.id = r.nameId GROUP BY r.nameId ORDER BY 3 DESC LIMIT {top}"))
    N = "NVTX_EVENTS"
    if N in tables:
        def nvtx() -> list[dict]:
            if "textId" in cols(N):
                name, join = "coalesce(n.text, s.value)", "LEFT JOIN StringIds s ON s.id = n.textId"
            else:
                name, join = "n.text", ""
            return grouped(f"SELECT {name} AS label, count(*), sum(n.end - n.start), max(n.end - n.start) "
                           f"FROM {N} n {join} WHERE n.end > n.start GROUP BY label ORDER BY 3 DESC LIMIT {top}")
        section("nvtx", nvtx)
    O = "OSRT_API"
    if O in tables:
        section("os_runtime", lambda: grouped(
            f"SELECT s.value, count(*), sum(o.end - o.start), max(o.end - o.start) FROM {O} o "
            f"JOIN StringIds s ON s.id = o.nameId GROUP BY o.nameId ORDER BY 3 DESC LIMIT {top}"))
    if "SAMPLING_CALLCHAINS" in tables:   # leaf functions of the CPU samples (--sample process-tree)
        section("cpu_samples", lambda: [
            {"function": f, "samples": n} for f, n in con.execute(
                "SELECT s.value, count(*) FROM SAMPLING_CALLCHAINS c JOIN StringIds s ON s.id = c.symbol "
                "WHERE c.stackDepth = 0 GROUP BY c.symbol ORDER BY 2 DESC LIMIT ?", (top,))])
    if "PROCESSES" in tables:
        section("processes", lambda: [{"pid": pid, "name": n} for pid, n in
                                      con.execute("SELECT pid, name FROM PROCESSES ORDER BY pid")])
    if not out["errors"]:
        del out["errors"]
    return out


# ------------------------------------------------------------------------------------------- tegrastats
_TS = {
    "ram": re.compile(r"\bRAM (\d+)/(\d+)MB"),
    "swap": re.compile(r"\bSWAP (\d+)/(\d+)MB"),
    "cpu": re.compile(r"\bCPU \[([^\]]*)\]"),
    "gpu": re.compile(r"\bGR3D_FREQ (\d+)%(?:@\[?(\d+)\]?)?"),
    "emc": re.compile(r"\bEMC_FREQ (\d+)%(?:@(\d+))?"),
    "temp": re.compile(r"\b([A-Za-z][\w]*)@(-?\d+(?:\.\d+)?)C\b"),
    "rail": re.compile(r"\b([A-Z][A-Z0-9_]+) (\d+)mW/(\d+)mW"),
}


def parse_tegrastats(line: str) -> dict | None:
    """One tegrastats line -> flat dict (ram_used_mb, cpu_avg_pct, gpu_pct, temp_<zone>_c, power_<rail>_mw ...)."""
    m = _TS["ram"].search(line)
    if not m:
        return None
    s: dict = {"ram_used_mb": int(m[1]), "ram_total_mb": int(m[2])}
    if m := _TS["swap"].search(line):
        s["swap_used_mb"] = int(m[1])
    if m := _TS["cpu"].search(line):
        loads, freqs = [], []
        for core in m[1].split(","):
            pct, sep, mhz = core.strip().partition("%@")
            if sep and pct.isdigit():
                loads.append(int(pct))
                if mhz.isdigit():
                    freqs.append(int(mhz))
        if loads:
            s.update(cpu_avg_pct=round(statistics.fmean(loads), 1), cpu_max_pct=max(loads), cpu_online=len(loads))
        if freqs:
            s["cpu_mhz"] = max(freqs)
    if m := _TS["gpu"].search(line):
        s["gpu_pct"] = int(m[1])
        if m[2]:
            s["gpu_mhz"] = int(m[2])
    if m := _TS["emc"].search(line):
        s["emc_pct"] = int(m[1])
    for zone, temp in _TS["temp"].findall(line):
        s[f"temp_{zone.lower()}_c"] = float(temp)
    for rail, cur, _avg in _TS["rail"].findall(line):
        s[f"power_{rail.lower()}_mw"] = int(cur)
    return s


def tegrastats_summary(log: Path, interval_ms: int) -> dict:
    samples = [s for s in map(parse_tegrastats, log.read_text(errors="replace").splitlines()) if s]
    keys = sorted({k for s in samples for k in s})
    with open(log.with_suffix(".csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["t_s", *keys])
        w.writeheader()
        for i, s in enumerate(samples):
            w.writerow({"t_s": round(i * interval_ms / 1000, 3), **s})
    metrics = {}
    for k in keys:
        vals = [s[k] for s in samples if k in s]
        metrics[k] = {"mean": round(statistics.fmean(vals), 2), "min": min(vals), "max": max(vals)}
    out = {"samples": len(samples), "interval_ms": interval_ms, "duration_s": len(samples) * interval_ms / 1000,
           "metrics": metrics, "csv": log.with_suffix(".csv").name}
    vin = [s["power_vdd_in_mw"] for s in samples if "power_vdd_in_mw" in s]
    if vin:   # board input power integrated over the recording
        out["energy_j"] = round(sum(vin) * interval_ms / 1e6, 2)
    return out


def _sample_line(s: dict) -> str:
    parts = [f"{s.get('t_s', 0):7.1f}s"]
    if "cpu_avg_pct" in s:
        parts.append(f"CPU {s['cpu_avg_pct']:4.0f}% (max {s['cpu_max_pct']:3d}%) @{s.get('cpu_mhz', '?')}MHz")
    if "gpu_pct" in s:
        parts.append(f"GPU {s['gpu_pct']:3d}%" + (f" @{s['gpu_mhz']}MHz" if "gpu_mhz" in s else ""))
    if "emc_pct" in s:
        parts.append(f"EMC {s['emc_pct']:3d}%")
    parts.append(f"RAM {s['ram_used_mb'] / 1024:.1f}/{s['ram_total_mb'] / 1024:.1f}G")
    if "power_vdd_in_mw" in s:
        parts.append(f"VDD_IN {s['power_vdd_in_mw'] / 1000:5.2f}W")
    temps = [v for k, v in s.items() if k.startswith("temp_")]
    if temps:
        parts.append(f"max {max(temps):.1f}C")
    return "  ".join(parts)


def cmd_tegrastats(p, a) -> None:
    board = J.Board(a.name, p)
    if a.live:
        proc = subprocess.Popen(["ssh", *board.ssh_opts, board.target, f"exec tegrastats --interval {a.interval}"],
                                stdout=subprocess.PIPE, text=True)
        t = 0.0
        try:
            for line in proc.stdout:
                s = parse_tegrastats(line)
                if s is None:
                    continue
                s["t_s"] = round(t, 3)
                print(json.dumps(s) if a.json else _sample_line(s), flush=True)
                t += a.interval / 1000
                if a.duration and t >= a.duration:
                    break
        except KeyboardInterrupt:
            pass
        finally:
            proc.terminate()
        return
    local, remote = _new_trace(board, "tegrastats", a.label)
    log = f"{remote}/tegrastats.log"
    if a.cmd:
        ws = Path(a.ws or Path.cwd()).resolve()
        run = (["timeout", "--foreground", "-s", "INT", str(a.duration)] if a.duration else []) + \
            J.board_launch_args(board, ws, a.cmd)
        script = _on_board(p, board, ws, shlex.join(run), remote, _tty(), tegrastats=True, interval=a.interval)
    else:
        command = f"timeout {a.duration or 10} tegrastats --interval {a.interval} --logfile {q(log)}; true"
        script = f"bash -c {q(f'mkdir -p {q(remote)}; {command}')}"
    J.info(f"tegrastats on {board.name} every {a.interval} ms"
           + (f" while running: {shlex.join(a.cmd)}" if a.cmd else f" for {a.duration or 10} s"))
    started, t0 = _now(), time.time()
    rc = board.ssh(locked(board, f"jxb trace tegrastats {local.name}", script, a), tty=_tty() and bool(a.cmd),
                   check=False)
    _save(board, p, a, "tegrastats", local, remote, started, time.time() - t0, rc, interval_ms=a.interval)


# ------------------------------------------------------------------------------------------ kernel trace
FTRACE_RE = re.compile(r"^\s*(?P<comm>.+?)-(?P<pid>\d+)\s+(?:\(\s*(?P<tgid>[\d-]+)\)\s+)?\[(?P<cpu>\d+)\]\s+"
                       r"(?:[\w.]{4,8}\s+)?(?P<ts>\d+\.\d+):\s+(?P<event>\w+):\s?(?P<args>.*)$")
SWITCH_RE = re.compile(r"prev_comm=(?P<pc>.*?) prev_pid=(?P<pp>-?\d+) prev_prio=(?P<ppr>-?\d+) "
                       r"prev_state=(?P<ps>\S+) ==> next_comm=(?P<nc>.*?) next_pid=(?P<np>-?\d+) "
                       r"next_prio=(?P<npr>-?\d+)")
WAKEUP_RE = re.compile(r"comm=(?P<comm>.*?) pid=(?P<pid>\d+) prio=(?P<prio>-?\d+)")
IRQ_RE = re.compile(r"irq=(?P<irq>\d+)(?: name=(?P<name>\S+))?")
SOFTIRQ_RE = re.compile(r"vec=(?P<vec>\d+) \[action=(?P<action>\w+)\]")


def _pct(vals: list[float], p: float) -> float:
    return vals[min(len(vals) - 1, int(p * len(vals)))]


class SchedStats:
    """Scheduler and IRQ statistics from ftrace text lines: per-task CPU time, context switches, wakeup latency
    (sched_wakeup -> sched_switch in), per-CPU busy time, IRQ/softirq handler times."""

    def __init__(self) -> None:
        self.comm: dict[int, str] = {}
        self.tgid: dict[int, int] = {}
        self.cur: dict[int, tuple[int, float]] = {}   # cpu -> (pid running, since)
        self.reset(None)

    def reset(self, ts: float | None) -> None:
        """Starts a new statistics window (live view); the running tasks carry over."""
        self.t0 = self.t1 = ts
        self.cur = {cpu: (pid, ts) for cpu, (pid, _) in self.cur.items()} if ts is not None else {}
        self.run: dict[int, float] = defaultdict(float)
        self.idle: dict[int, float] = defaultdict(float)
        self.switches: dict[int, int] = defaultdict(int)
        self.preempted: dict[int, int] = defaultdict(int)
        self.woken: dict[int, float] = {}
        self.lat: dict[int, list[float]] = defaultdict(list)
        self.irq_open: dict[tuple[int, str], float] = {}
        self.irqs: dict[str, list] = defaultdict(lambda: [0, 0.0, 0.0])   # name -> [count, total s, max s]
        self.events: dict[str, int] = defaultdict(int)
        self.ctxsw = 0

    def feed(self, line: str) -> float | None:
        """Consumes one line; returns its timestamp if it was a trace event."""
        m = FTRACE_RE.match(line)
        if not m:
            return None
        ts, cpu, ev = float(m["ts"]), int(m["cpu"]), m["event"]
        if self.t0 is None:
            self.t0 = ts
        self.t1 = ts
        self.events[ev] += 1
        if m["tgid"] and m["tgid"].strip("-"):
            self.tgid[int(m["pid"])] = int(m["tgid"])
        if ev == "sched_switch":
            s = SWITCH_RE.search(m["args"])
            if s:
                self._switch(ts, cpu, int(s["pp"]), s["pc"], s["ps"], int(s["np"]), s["nc"])
        elif ev in ("sched_wakeup", "sched_wakeup_new"):
            w = WAKEUP_RE.search(m["args"])
            if w:
                pid = int(w["pid"])
                self.comm.setdefault(pid, w["comm"])
                self.woken.setdefault(pid, ts)
        elif ev in ("irq_handler_entry", "irq_handler_exit"):
            i = IRQ_RE.search(m["args"])
            if i:
                self._irq(ts, cpu, f"irq{i['irq']}" + (f" {i['name']}" if i["name"] else ""), i["irq"],
                          ev.endswith("entry"))
        elif ev in ("softirq_entry", "softirq_exit"):
            i = SOFTIRQ_RE.search(m["args"])
            if i:
                self._irq(ts, cpu, f"softirq {i['action']}", f"s{i['vec']}", ev.endswith("entry"))
        return ts

    def _switch(self, ts, cpu, prev, prev_comm, prev_state, nxt, next_comm) -> None:
        self.ctxsw += 1
        if cpu in self.cur:
            pid, since = self.cur[cpu]
            if since is not None:
                (self.idle if pid == 0 else self.run)[cpu if pid == 0 else pid] += ts - since
        self.comm[prev], self.comm[nxt] = prev_comm, next_comm
        if prev and prev_state.startswith("R"):
            self.preempted[prev] += 1
        self.cur[cpu] = (nxt, ts)
        if nxt:
            self.switches[nxt] += 1
            woken = self.woken.pop(nxt, None)
            if woken is not None:
                self.lat[nxt].append(ts - woken)

    def _irq(self, ts, cpu, name, key, entry: bool) -> None:
        if entry:
            self.irq_open[(cpu, key)] = ts
            self.irq_open[(cpu, "name" + key)] = name   # the exit event carries no name
            return
        start = self.irq_open.pop((cpu, key), None)
        name = self.irq_open.pop((cpu, "name" + key), name)
        if start is not None:
            st = self.irqs[name]
            st[0] += 1
            st[1] += ts - start
            st[2] = max(st[2], ts - start)

    def summary(self, top: int = 15) -> dict:
        span = (self.t1 - self.t0) if self.t0 is not None else 0.0
        run, idle = defaultdict(float, self.run), defaultdict(float, self.idle)
        for cpu, (pid, since) in self.cur.items():   # tasks still running at the end of the window
            if since is not None:
                (idle if pid == 0 else run)[cpu if pid == 0 else pid] += self.t1 - since
        tasks = []
        for pid, t in sorted(run.items(), key=lambda kv: -kv[1])[:top]:
            lat = sorted(self.lat.get(pid, []))
            tasks.append({"pid": pid, "tgid": self.tgid.get(pid), "comm": self.comm.get(pid, "?"),
                          "cpu_ms": round(t * 1e3, 3), "cpu_pct": round(100 * t / span, 2) if span else 0.0,
                          "switches": self.switches.get(pid, 0), "preempted": self.preempted.get(pid, 0),
                          "wakeup_us": {"n": len(lat), "p50": round(_pct(lat, 0.5) * 1e6, 1),
                                        "p99": round(_pct(lat, 0.99) * 1e6, 1),
                                        "max": round(lat[-1] * 1e6, 1)} if lat else None})
        procs: dict[int, float] = defaultdict(float)
        label: dict[int, str] = {}   # process name: its main thread's, else its busiest thread's
        for pid, t in sorted(run.items(), key=lambda kv: -kv[1]):
            tg = self.tgid.get(pid, pid)
            procs[tg] += t
            label.setdefault(tg, self.comm.get(tg) or self.comm.get(pid, "?"))
        worst = sorted(((max(v), pid) for pid, v in self.lat.items() if v), reverse=True)[:top]
        return {
            "span_s": round(span, 6),
            "events": dict(self.events),
            "ctxsw_per_s": round(self.ctxsw / span, 1) if span else None,
            "cpu_busy_pct": {cpu: round(100 * (1 - idle[cpu] / span), 1) if span else None
                             for cpu in sorted(set(self.cur) | set(idle))},
            "top_tasks": tasks,
            "top_processes": [{"tgid": tg, "comm": label[tg], "cpu_ms": round(t * 1e3, 3),
                               "cpu_pct": round(100 * t / span, 2) if span else 0.0}
                              for tg, t in sorted(procs.items(), key=lambda kv: -kv[1])[:top]],
            "worst_wakeup_latency_us": [{"pid": pid, "comm": self.comm.get(pid, "?"), "max": round(m * 1e6, 1)}
                                        for m, pid in worst],
            "irqs": [{"name": n, "count": c, "total_ms": round(t * 1e3, 3), "max_us": round(mx * 1e6, 1)}
                     for n, (c, t, mx) in sorted(self.irqs.items(), key=lambda kv: -kv[1][1])[:top]],
        }


def kernel_summary(path: Path) -> dict:
    st = SchedStats()
    with open(path, errors="replace") as f:
        for line in f:
            st.feed(line)
    return st.summary()


def _helper_cmd(board, a) -> tuple[list[str], str]:
    """Copies jxb-ftrace.sh to the board and returns the root prefix that runs it (sudo, docker or as is)."""
    root = _trace_root(board)
    helper = f"{root}/jxb-ftrace.sh"
    board.ssh(f"mkdir -p {q(root)}")
    J.sh(["rsync", "-a", "-e", shlex.join(["ssh", *board.ssh_opts]), str(J.REPO / "tools/board/jxb-ftrace.sh"),
          f"{board.target}:{helper}"])
    mode = a.root or board.cfg.get("trace_root", "sudo")
    if mode == "sudo":
        return ["sudo", "-p", f"[jxb] sudo password for %u on {board.name} (kernel tracing): ", "sh"], helper
    if mode == "docker":   # a privileged container: for users in the board's docker group
        image = board.cfg.get("trace_image")
        if not image:
            J.fail('--root docker needs trace_image = "<image with /bin/sh>" for the board in boards.toml')
        return ["docker", "run", "--rm", "-i", "--privileged", "-v", "/sys/kernel/tracing:/sys/kernel/tracing",
                "-v", f"{root}:{root}", "--entrypoint", "sh", image], helper
    return ["sh"], helper


def _expand_events(spec: str) -> list[str]:
    out: list[str] = []
    for tok in filter(None, (t.strip() for t in spec.split(","))):
        out += [e for e in KERNEL_EVENTS.get(tok, [tok]) if e not in out]
    return out


def _live_top(board, a, remote_cmd: list[str]) -> int:
    """Live scheduler view from trace_pipe: one statistics window per second of trace time."""
    proc = subprocess.Popen(["ssh", *board.ssh_opts, "-tt", board.target, shlex.join(remote_cmd)],
                            stdout=subprocess.PIPE)   # stdin stays the terminal: sudo's password prompt
    raw = _tty()   # ssh -tt puts the local terminal in raw mode: lines need \r\n
    nl = "\r\n" if raw else "\n"
    st, buf, window = SchedStats(), b"", None
    try:
        while chunk := os.read(proc.stdout.fileno(), 65536):
            buf += chunk
            *lines, buf = buf.split(b"\n")
            if b"password" in buf:   # prompt without newline
                sys.stdout.write(buf.decode(errors="replace"))
                sys.stdout.flush()
                buf = b""
            for raw_line in lines:
                line = raw_line.decode(errors="replace").rstrip("\r")
                ts = st.feed(line)
                if ts is None:
                    if line and not line.startswith("#"):
                        sys.stdout.write(line + nl)
                    continue
                window = ts if window is None else window
                if ts - window >= 1.0:
                    s = st.summary(top=12)
                    if a.json:
                        sys.stdout.write(json.dumps(s) + nl)
                    else:
                        sys.stdout.write(("\033[2J\033[H" if raw else "") + format_kernel(s, live=True)
                                         .replace("\n", nl) + nl)
                    sys.stdout.flush()
                    st.reset(ts)
                    window = ts
    except KeyboardInterrupt:
        proc.terminate()
    return proc.wait()


def cmd_kernel(p, a) -> None:
    board = J.Board(a.name, p)
    events = _expand_events(a.events)
    prefix, helper = _helper_cmd(board, a)
    if a.live:
        stream = prefix + [helper, "stream", *events]
        if a.top:
            rc = _live_top(board, a, stream)
        else:
            rc = J.sh(["ssh", *board.ssh_opts, "-tt", board.target, shlex.join(stream)], check=False).returncode
        sys.exit(0 if rc in (0, 130) else rc)
    local, remote = _new_trace(board, "kernel", a.label)
    out = f"{remote}/kernel.ftrace.txt"
    record = shlex.join(prefix + [helper, "record", str(a.duration), out, *events])
    if a.cmd:
        # Recorder in the background, workload in the foreground. sudo asks first (-v), the background sudo then
        # uses the cached credentials of this terminal. SIGTERM ends the recording early once the workload exits.
        ws = Path(a.ws or Path.cwd()).resolve()
        auth = shlex.join(prefix[:3] + ["-v"]) + " && " if prefix[0] == "sudo" else ""
        bg = record.replace("sudo -p", "sudo -n -p", 1) if prefix[0] == "sudo" else record
        workload = J.board_exec(p, board, J.board_env_script(p, board, ws)
                                + shlex.join(J.board_launch_args(board, ws, a.cmd)), tty=True)
        command = (f"{auth}{{ {bg} & JXB_FT=$!; sleep 1; {workload}; "
                   "rc=$?; kill -TERM $JXB_FT 2>/dev/null; wait $JXB_FT; (exit $rc); }")   # no exit: cleanups follow
    else:
        command = record
    if a.tegrastats:
        command = _with_tegrastats(f"{remote}/tegrastats.log", a.interval, command)
    J.info(f"kernel trace on {board.name}: {', '.join(events)}"
           + (f" for {a.duration} s" if a.duration else " until Ctrl-C"))
    started, t0 = _now(), time.time()
    rc = board.ssh(locked(board, f"jxb trace kernel {local.name}", f"bash -c {q(f'mkdir -p {q(remote)}; {command}')}",
                          a), tty=True, check=False)
    if rc == 77:
        board.ssh(f"rm -rf {q(remote)}", check=False)
        J.fail("kernel tracing needs root on the board: run in a terminal (sudo asks for your password), or use "
               "--root docker (docker group) / --root none (tracefs made accessible), see docs/workflows.md")
    _save(board, p, a, "kernel", local, remote, started, time.time() - t0, rc, events=events,
          interval_ms=a.interval if a.tegrastats else None)


# ------------------------------------------------------------------------------------------- benchmark
BENCH_BACKENDS = "ort-cpu,ort-cuda,ort-trt,ort-trt-fp16,ort-trt-int8,trtexec-fp32,trtexec-fp16,trtexec-int8"


def cmd_bench(p, a) -> None:
    """Inference benchmark matrix (tools/board/jxb_bench.sh): ONNX Runtime with the CPU, CUDA and TensorRT EPs (jxb's
    build, from the profile's test stage) and native TensorRT engines (trtexec), fp32/fp16 and INT8 for Q/DQ models.
    The engines stay on the board in <base>/bench/engines."""
    board = J.Board(a.name, p)
    stage = J.REPO / ".work/tests" / p.name / "stage"
    if not (stage / "bin/ort_bench").is_file():
        J.fail(f"no ort_bench for {p.name} yet: run jxb --profile {p.name} test")
    models = [Path(m).resolve() for m in a.models]
    missing = [str(m) for m in models if not m.is_file()]
    if missing:
        J.fail(f"not found: {', '.join(missing)}")
    # unique names from the model's directory and file (models/S0_fp32_ref/best.onnx -> S0_fp32_ref-best)
    names = [f"{m.parent.name}-{m.stem}" for m in models]
    local, remote = _new_trace(board, "bench", a.label)
    root = f"{board.base}/bench"
    e = shlex.join(["ssh", *board.ssh_opts])
    board.ssh(f"mkdir -p {q(root)}/models {q(root)}/ort_bench/bin {q(root)}/ort_bench/lib {q(remote)}")
    J.sh(["rsync", "-a", "-e", e, str(stage / "bin/ort_bench"), f"{board.target}:{root}/ort_bench/bin/"])
    J.sh(["rsync", "-a", "-e", e, *map(str, sorted((stage / "lib").glob("libonnxruntime*"))),
          f"{board.target}:{root}/ort_bench/lib/"])
    J.sh(["rsync", "-a", "-e", e, str(J.REPO / "tools/board/jxb_bench.sh"), f"{board.target}:{root}/"])
    for m, n in zip(models, names):
        J.sh(["rsync", "-a", "-e", e, str(m), f"{board.target}:{root}/models/{n}.onnx"])
    backends = " ".join(b.strip() for b in a.backends.split(","))
    run = (f"JXB_BENCH_BACKENDS={q(backends)} JXB_BENCH_ITERS={a.iters} bash {q(root)}/jxb_bench.sh {q(remote)} "
           f"{q(root)}/engines {q(root)}/ort_bench {q(board.cfg.get('trt_cache', f'/home/{board.user}/jxb/trt_cache'))} "
           + " ".join(q(f"{root}/models/{n}.onnx") for n in names))
    command = _with_tegrastats(f"{remote}/tegrastats.log", a.interval, run)
    J.info(f"benchmark on {board.name}: {len(models)} model(s) x {backends} (engines are built on the first run)")
    started, t0 = _now(), time.time()
    rc = board.ssh(locked(board, f"jxb trace bench {local.name}", f"bash -c {q(command)}", a), check=False)
    _save(board, p, a, "bench", local, remote, started, time.time() - t0, rc, models=names, backends=backends,
          interval_ms=a.interval)


def bench_summary(results: Path) -> dict:
    rows = [json.loads(l) for l in results.read_text().splitlines() if l.startswith("{")]
    by_model: dict[str, list] = defaultdict(list)
    for r in rows:
        by_model[r["model"]].append(r)
    for model_rows in by_model.values():   # speedup against ONNX Runtime's CUDA EP (fp32), the "no TensorRT" path
        base = next((r.get("p50_ms") for r in model_rows if r["backend"] == "ort-cuda" and r.get("p50_ms")), None)
        for r in model_rows:
            if base and r.get("p50_ms"):
                r["speedup_vs_ort_cuda"] = round(base / r["p50_ms"], 2)
    return {"runs": len(rows), "errors": [r for r in rows if "error" in r],
            "models": {m: sorted(rs, key=lambda r: r.get("p50_ms") or 1e9) for m, rs in by_model.items()}}


# ------------------------------------------------------------------------------- launch session analysis
# helpers of the recording itself, left out of the per-process view
MONITOR_COMMS = {"sleep", "cat", "cut", "tegrastats", "timeout", "tee", "bash", "sh", "ssh", "sshd", "docker",
                 "docker-init", "containerd-shim", "tini", "getconf"}


def procstat_summary(log: Path, sysconf: Path, top: int = 15) -> dict:
    """Per-process CPU (100 % = one core) and RSS from the 1 s /proc/<pid>/stat samples of `jxb launch --monitor`;
    `session` are the processes of the launch: descendants of the session's shell (R) or of its runtime container's
    init (C), for older recordings the processes started during the recording."""
    tck, page = 100, 4096
    if sysconf.exists():
        vals = sysconf.read_text().split()
        tck, page = int(vals[0]), int(vals[1])
    samples: list[tuple[float, dict]] = []
    roots: set[int] = set()
    for line in log.read_text(errors="replace").splitlines():
        if line.startswith("T "):
            samples.append((float(line[2:]), {}))
            continue
        if line[:2] in ("R ", "C ") and line[2:].strip().isdigit():
            roots.add(int(line[2:]))
            continue
        lp, rp = line.find("("), line.rfind(")")
        if not samples or lp < 1 or rp < lp:
            continue
        f = line[rp + 2:].split()
        if len(f) < 22:
            continue
        # fields after the comm: state ppid ... utime(14) stime(15) ... starttime(22) vsize rss(24)
        samples[-1][1][int(line[:lp])] = (line[lp + 1:rp], int(f[1]), int(f[11]) + int(f[12]), int(f[21]),
                                          int(f[19]))
    if len(samples) < 2:
        return {"samples": len(samples)}
    t0 = samples[0][0]
    series: dict[int, list] = defaultdict(list)
    meta: dict[int, tuple] = {}
    for t, procs in samples:
        for pid, (comm, ppid, ticks, rss, start) in procs.items():
            series[pid].append((t, ticks, rss))
            meta[pid] = (comm, ppid, start)

    def ours(pid: int) -> bool:
        if not roots:
            return meta[pid][2] / tck >= t0 - 1.0
        for _ in range(64):
            if pid in roots:
                return True
            if pid not in meta or pid <= 1:
                return False
            pid = meta[pid][1]
        return False

    rows = []
    span = samples[-1][0] - t0
    for pid, s in series.items():
        if len(s) < 2 or s[-1][0] <= s[0][0]:
            continue
        cpu = [100 * (k2 - k1) / tck / (t2 - t1) for (t1, k1, _), (t2, k2, _) in zip(s, s[1:]) if t2 > t1]
        comm, ppid, start = meta[pid]
        rows.append({"pid": pid, "ppid": ppid, "comm": comm, "session": ours(pid),
                     # share: CPU time over the whole recording (short-lived processes don't inflate totals);
                     # mean: while the process was alive
                     "cpu_share_pct": round(100 * (s[-1][1] - s[0][1]) / tck / span, 1),
                     "cpu_mean_pct": round(100 * (s[-1][1] - s[0][1]) / tck / (s[-1][0] - s[0][0]), 1),
                     "cpu_max_pct": round(max(cpu), 1) if cpu else 0.0,
                     "rss_max_mb": round(max(r for _, _, r in s) * page / 2 ** 20, 1),
                     "alive_s": round(s[-1][0] - s[0][0], 1),
                     "series": [[round(t - t0, 1), round(c, 1), round(r * page / 2 ** 20, 1)]
                                for (t, _, r), c in zip(s[1:], cpu)]})
    rows.sort(key=lambda r: -r["cpu_share_pct"])
    session = [r for r in rows if r["session"] and r["comm"] not in MONITOR_COMMS]
    keep = {id(r) for r in session[:top]} | {id(r) for r in rows[:top]}
    for r in rows:
        if id(r) not in keep:
            r.pop("series")   # time series only for the interesting processes
    return {"samples": len(samples), "duration_s": round(span, 1),
            "session_cpu_pct": round(sum(r["cpu_share_pct"] for r in session), 1),
            # the recording's own tools (sampler shell, tegrastats; the 1 s `cat` runs are too short to see)
            "monitor_cpu_pct": round(sum(r["cpu_share_pct"] for r in rows
                                         if r["session"] and r["comm"] in MONITOR_COMMS), 1),
            "session_rss_mb": round(sum(r["rss_max_mb"] for r in session), 1),
            "session": session[:top], "top": [r for r in rows if not r["session"]][:top]}


def meminfo_summary(log: Path) -> dict | None:
    """The system memory counters ("M" lines of `jxb launch --monitor`) over time, in MB, with their linear trend:
    memory growth that no process's RSS shows (page cache, shared memory, kernel or driver allocations)."""
    t, rows = None, []
    for line in log.read_text(errors="replace").splitlines():
        if line.startswith("T "):
            t = float(line[2:])
            rows.append({"t": t})
        elif line.startswith("M ") and rows:
            key, _, value = line[2:].partition(":")
            if value.split()[:1] and value.split()[0].isdigit():
                rows[-1][key.strip()] = int(value.split()[0]) / 1024
    rows = [r for r in rows if len(r) > 1]
    if len(rows) < 3:
        return None
    t0 = rows[0]["t"]
    keys = sorted({k for r in rows for k in r if k != "t"})
    with open(log.with_name("meminfo.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["t_s", *keys])
        w.writeheader()
        for r in rows:
            w.writerow({"t_s": round(r["t"] - t0, 1), **{k: round(r[k], 1) for k in keys if k in r}})
    out = {"samples": len(rows), "duration_s": round(rows[-1]["t"] - t0, 1), "csv": "meminfo.csv", "keys": {}}
    for k in keys:
        pts = [(r["t"] - t0, r[k]) for r in rows if k in r]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        slope = statistics.linear_regression(xs, ys).slope * 60 if len(set(xs)) > 1 and len(set(ys)) > 1 else 0.0
        out["keys"][k] = {"first_mb": round(ys[0], 1), "last_mb": round(ys[-1], 1), "min_mb": round(min(ys), 1),
                          "max_mb": round(max(ys), 1), "slope_mb_min": round(slope, 1)}
    return out


def rates_summary(log: Path, skip: int = 3) -> dict:
    """Per-second message rates of `jxb launch --rates` (jxb_rate_probe.py JSON lines) per topic; the first
    `skip` reports of a topic (discovery, start-up) are left out."""
    series: dict[str, list[float]] = defaultdict(list)
    for line in log.read_text(errors="replace").splitlines():
        if line.startswith('{"probe"'):
            r = json.loads(line)
            series[r["probe"]].append(r["hz"])
    out = {}
    for topic, hz in series.items():
        v = sorted(hz[skip:]) or sorted(hz)
        out[topic] = {"reports": len(hz), "median_hz": round(statistics.median(v), 2),
                      "mean_hz": round(statistics.fmean(v), 2), "p10_hz": _pct(v, 0.1), "p90_hz": _pct(v, 0.9),
                      "min_hz": v[0], "max_hz": v[-1]}
    return out


_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def launch_log_summary(log: Path) -> dict:
    """Processes started, finished and died (before or during shutdown), errors and warnings of a ros2 launch log."""
    text = _ANSI.sub("", log.read_text(errors="replace"))
    stop = min((m.start() for m in re.finditer(r"user interrupted with ctrl-c|signal_handler\(sig", text)),
               default=len(text))
    started = re.findall(r"\[INFO\] \[([^\]]+)\]: process started with pid \[(\d+)\]", text)
    died = [{"process": n, "pid": int(pid), "exit": int(code), "during_shutdown": m.start() > stop}
            for m in re.finditer(r"\[ERROR\] \[([^\]]+)\]: process has died \[pid (\d+), exit code (-?\d+)", text)
            for n, pid, code in [m.groups()]]
    lines = text.splitlines()
    errors = [l for l in lines if "[ERROR]" in l and "process has died" not in l]
    return {"processes": [n for n, _ in started],
            "finished_cleanly": len(re.findall(r"process has finished cleanly", text)),
            "died": died, "crashed": [d for d in died if not d["during_shutdown"]],
            "errors": len(errors), "warnings": sum("[WARN" in l for l in lines), "first_errors": errors[:8],
            "lines": len(lines)}


def bag_summary(meta: Path) -> dict:
    """Topics, message counts and rates from a rosbag2 metadata.yaml (no YAML library needed)."""
    text = meta.read_text(errors="replace")
    m = re.search(r"duration:\s*\n\s*nanoseconds:\s*(\d+)", text)
    duration = int(m[1]) / 1e9 if m else 0.0
    topics, cur = [], {}
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("name:"):
            cur = {"name": s.split(":", 1)[1].strip()}
        elif s.startswith("type:") and "name" in cur and "type" not in cur:
            cur["type"] = s.split(":", 1)[1].strip()
        elif s.startswith("message_count:") and "name" in cur:
            n = int(s.split(":", 1)[1])
            topics.append({"topic": cur["name"], "type": cur.get("type"), "messages": n,
                           "hz": round(n / duration, 2) if duration else None})
            cur = {}
    series = {}
    for f in sorted((meta.parent / "csv").glob("*.csv")):   # from tools/board/jxb_bag_csv.py
        with open(f) as fh:
            rows = list(csv.DictReader(fh))
        for col in ("value", "latency_ms"):
            vals = sorted(float(r[col]) for r in rows if r[col] not in ("", "None"))
            if vals:
                series[f"/{f.stem.replace('__', '/')}:{col}"] = {
                    "n": len(vals), "mean": round(statistics.fmean(vals), 3), "p50": round(_pct(vals, 0.5), 3),
                    "p90": round(_pct(vals, 0.9), 3), "p99": round(_pct(vals, 0.99), 3), "max": round(vals[-1], 3)}
    return {"duration_s": round(duration, 2), "messages": sum(t["messages"] for t in topics),
            "topics": sorted(topics, key=lambda t: t["topic"]), "series": series}


# ------------------------------------------------------------------------------------------ remote GUI
def cmd_gui(p, a) -> None:
    """The board's own Nsight Systems/Compute GUI as a window on this desktop (SSH X11 forwarding): it profiles
    live on the board, nothing to install on the host. Trusted forwarding (-Y), since hosts often lack xauth."""
    if not os.environ.get("DISPLAY"):
        J.fail("no X display on this host (DISPLAY is unset; on Wayland, XWayland provides one)")
    board = J.Board(a.name, p)
    ws = Path(a.ws or Path.cwd()).resolve()
    tool = board.cfg.get(f"{a.tool}_ui", f"/usr/local/cuda/bin/{a.tool}-ui")
    script = J.board_env_script(p, board, ws) + shlex.join([tool, *a.cmd])
    J.info(f"{Path(tool).name} on {board.name}, displayed here")
    sys.exit(J.sh(["ssh", *board.ssh_opts, "-Y", board.target, f"bash -lc {q(script)}"], check=False).returncode)


# --------------------------------------------------------------------------------------- report / list
def analyze(d: Path) -> dict:
    meta = json.loads((d / "meta.json").read_text()) if (d / "meta.json").exists() else {}
    s: dict = {"trace": d.name, "dir": str(d), "meta": meta}
    if (d / "report.sqlite").exists():
        s["nsys"] = nsys_summary(d / "report.sqlite")
        s["nsys"]["report"] = "report.nsys-rep"
    elif (d / "report.nsys-rep").exists():
        s["nsys"] = {"error": "no SQLite export (nsys export failed on the board)", "report": "report.nsys-rep"}
    if (d / "tegrastats.log").exists():
        s["tegrastats"] = tegrastats_summary(d / "tegrastats.log", meta.get("interval_ms") or 500)
    if (d / "kernel.ftrace.txt").exists():
        s["kernel"] = kernel_summary(d / "kernel.ftrace.txt")
        s["kernel"]["file"] = "kernel.ftrace.txt"
    if (d / "results.jsonl").exists():
        s["bench"] = bench_summary(d / "results.jsonl")
    if (d / "launch.log").exists():
        s["launch"] = launch_log_summary(d / "launch.log")
    if (d / "procstat.log").exists():
        s["processes"] = procstat_summary(d / "procstat.log", d / "sysconf")
        mem = meminfo_summary(d / "procstat.log")
        if mem:
            s["meminfo"] = mem
    if (d / "rates.log").exists():
        s["rates"] = rates_summary(d / "rates.log")
    if (d / "memguard.log").exists():
        s["memguard"] = (d / "memguard.log").read_text(errors="replace").strip()
    if (d / "bag/metadata.yaml").exists():
        s["bag"] = bag_summary(d / "bag/metadata.yaml")
    helpers = sorted(d.glob("with*.log"))
    if helpers:
        s["helpers"] = {h.stem: _ANSI.sub("", h.read_text(errors="replace")).splitlines()[-3:] for h in helpers}
    (d / "summary.json").write_text(json.dumps(s, indent=2) + "\n")
    return s


def _table(rows: list[dict], cols: list[tuple[str, str, str]], limit: int = 10) -> list[str]:
    """cols: (key, header, format) -> aligned text lines."""
    width = {k: len(format(0, f)) for k, _, f in cols if f}
    out = ["  " + "  ".join(f"{h:>{width[k]}}" if f else h for k, h, f in cols)]
    for r in rows[:limit]:
        out.append("  " + "  ".join((format(r[k], f) if r.get(k) is not None else f"{'-':>{width[k]}}") if f
                                    else str(r.get(k)) for k, _, f in cols))
    return out


def format_kernel(k: dict, live: bool = False) -> str:
    lines = [f"kernel: {k['span_s']:.2f} s, {k['ctxsw_per_s'] or 0:.0f} ctxsw/s, CPU busy "
             + " ".join(f"cpu{c} {v:.0f}%" for c, v in k["cpu_busy_pct"].items() if v is not None)]
    rows = [{**t, "p99": (t["wakeup_us"] or {}).get("p99"), "max": (t["wakeup_us"] or {}).get("max")}
            for t in k["top_tasks"]]
    lines += _table(rows, [("cpu_pct", "cpu%", "6.2f"), ("switches", "switches", "8d"),
                           ("preempted", "preempt", "7d"), ("p99", "wake p99us", "10.1f"),
                           ("max", "wake max us", "11.1f"), ("pid", "pid", "7d"), ("comm", "task", "")],
                    limit=12 if live else 15)
    if k["irqs"] and not live:
        lines.append("  IRQs:")
        lines += _table(k["irqs"], [("total_ms", "total ms", "8.3f"), ("count", "count", "7d"),
                                    ("max_us", "max us", "8.1f"), ("name", "handler", "")])
    return "\n".join(lines)


def format_summary(s: dict) -> str:
    m = s.get("meta", {})
    lines = [f"{s['trace']}  board={m.get('board')} profile={m.get('profile')} rc={m.get('rc')} "
             f"wall={m.get('wall_s')} s", f"  command: {shlex.join(m.get('command') or []) or '-'}"]
    n = s.get("nsys")
    if n:
        if "error" in n:
            lines.append(f"nsys: {n['error']}")
        g = n.get("gpu")
        if g:
            lines.append(f"GPU: {g['kernel_launches']} kernel launches, {g['kernel_time_ms']:.1f} ms kernel time, "
                         f"busy {g['busy_pct']}% of {n['span_s']:.2f} s")
            lines += _table(g["top_kernels"], [("total_ms", "total ms", "9.2f"), ("pct", "%", "5.1f"),
                                               ("count", "count", "6d"), ("avg_us", "avg us", "8.1f"),
                                               ("name", "kernel", "")])
        for mc in n.get("memcpy", []):
            lines.append(f"memcpy {mc['kind']}: {mc['count']}x, {mc['mb']:.1f} MB, {mc['total_ms']:.2f} ms")
        for key, title in (("cuda_api", "CUDA API"), ("nvtx", "NVTX ranges"), ("os_runtime", "OS runtime")):
            if n.get(key):
                lines.append(f"{title}:")
                lines += _table(n[key], [("total_ms", "total ms", "9.2f"), ("count", "count", "7d"),
                                         ("avg_us", "avg us", "9.1f"), ("name", "name", "")], limit=8)
        if n.get("cpu_samples"):
            lines.append("CPU samples (leaf functions):")
            lines += _table(n["cpu_samples"], [("samples", "samples", "7d"), ("function", "function", "")], 8)
        for e in n.get("errors", []):
            lines.append(f"  (skipped {e})")
    t = s.get("tegrastats")
    if t:
        mt = t["metrics"]

        def stat(k: str, scale: float = 1.0, unit: str = "") -> str:
            return f"{mt[k]['mean'] * scale:.1f}{unit} (max {mt[k]['max'] * scale:.1f}{unit})" if k in mt else "-"
        temps = [k for k in mt if k.startswith("temp_")]
        lines.append(f"tegrastats: {t['samples']} samples, {t['duration_s']:.1f} s; CPU {stat('cpu_avg_pct', unit='%')}"
                     f", GPU {stat('gpu_pct', unit='%')}, RAM {stat('ram_used_mb', 1 / 1024, 'G')}, "
                     f"VDD_IN {stat('power_vdd_in_mw', 1e-3, 'W')}"
                     + (f", energy {t['energy_j']} J" if "energy_j" in t else "")
                     + (f", hottest {max(mt[k]['max'] for k in temps):.1f}C" if temps else ""))
    be = s.get("bench")
    if be:
        for model, rows in be["models"].items():
            lines.append(f"{model}:")
            lines += _table(rows, [("p50_ms", "p50 ms", "8.2f"), ("p99_ms", "p99 ms", "8.2f"), ("fps", "FPS", "7.1f"),
                                   ("speedup_vs_ort_cuda", "x CUDA", "6.2f"), ("backend", "backend", "")], limit=12)
        for r in be["errors"]:
            lines.append(f"  FAILED {r['model']} {r['backend']}: {r['error']}")
    la = s.get("launch")
    if la:
        lines.append(f"launch ({m.get('mode', '?')}): {len(la['processes'])} processes started, "
                     f"{la['finished_cleanly']} finished cleanly, {len(la['crashed'])} crashed before shutdown, "
                     f"{la['errors']} error and {la['warnings']} warning lines")
        for c in la["crashed"]:
            lines.append(f"  CRASHED {c['process']} (pid {c['pid']}, exit code {c['exit']})")
        for e in la["first_errors"][:5]:
            lines.append(f"  {e[:160]}")
    pr = s.get("processes")
    if pr and pr.get("session"):
        lines.append(f"processes of the session: {pr['session_cpu_pct']}% CPU (100% = 1 core), "
                     f"{pr['session_rss_mb']} MB RSS, over {pr['duration_s']} s")
        lines += _table(pr["session"], [("cpu_share_pct", "cpu%", "6.1f"), ("cpu_mean_pct", "alive%", "6.1f"),
                                        ("cpu_max_pct", "max%", "6.1f"), ("rss_max_mb", "RSS MB", "7.1f"),
                                        ("alive_s", "alive s", "7.1f"), ("pid", "pid", "7d"),
                                        ("comm", "process", "")], limit=12)
    mi = s.get("meminfo")
    if mi:
        def trend(k: str) -> str:
            v = mi["keys"].get(k)
            return f"{k} {v['first_mb']:.0f} -> {v['last_mb']:.0f} MB ({v['slope_mb_min']:+.0f} MB/min)" if v else ""
        lines.append("memory: " + ", ".join(x for x in map(trend, ("MemAvailable", "Cached", "Shmem", "Slab",
                                                                    "SUnreclaim")) if x))
        nvmap = [trend(k) for k in mi["keys"] if k.startswith("NvMap")]
        if nvmap:
            lines.append("  " + ", ".join(nvmap))
    if s.get("memguard"):
        lines.append(f"  {s['memguard']}")
    for topic, r in (s.get("rates") or {}).items():
        lines.append(f"rate {topic}: median {r['median_hz']:.1f} Hz (p10 {r['p10_hz']:.1f}, p90 {r['p90_hz']:.1f}, "
                     f"{r['reports']} reports)")
    b = s.get("bag")
    if b:
        lines.append(f"bag: {b['messages']} messages in {b['duration_s']} s")
        lines += _table(b["topics"], [("hz", "Hz", "8.2f"), ("messages", "msgs", "7d"), ("topic", "topic", "")],
                        limit=20)
        if b.get("series"):
            lines.append("  numeric topics (value or header latency):")
            lines += _table([{"name": k, **v} for k, v in b["series"].items()],
                            [("mean", "mean", "9.3f"), ("p50", "p50", "9.3f"), ("p99", "p99", "9.3f"),
                             ("max", "max", "9.3f"), ("n", "n", "6d"), ("name", "topic:field", "")], limit=20)
    for name, tail in (s.get("helpers") or {}).items():
        lines.append(f"{name}: {' | '.join(t.strip()[:100] for t in tail)}")
    k = s.get("kernel")
    if k:
        lines.append(format_kernel(k))
        lines.append(f"  open {s['dir']}/kernel.ftrace.txt in https://ui.perfetto.dev for the timeline")
    if n and n.get("report"):
        lines.append(f"  Nsight Systems GUI: {s['dir']}/{n['report']}")
    return "\n".join(lines)


def _all_traces() -> list[Path]:
    return sorted((d for d in (J.REPO / "traces").glob("*/*") if d.is_dir() and d.parent.name != "compare"),
                  key=lambda d: d.name)


def cmd_report(p, a) -> None:
    target = a.target or "latest"
    if Path(target).is_dir():
        d = Path(target).resolve()
    else:
        pool = _all_traces() if target == "latest" else sorted((J.REPO / "traces" / target).glob("*"))
        if not pool:
            J.fail(f"no traces for {target} (see: jxb trace list)")
        d = pool[-1]
    s = analyze(d)
    print(json.dumps(s, indent=2) if a.json else format_summary(s))


def _resolve(target: str | None) -> Path:
    """A trace directory from DIR, a board name (its latest recording) or `latest`."""
    target = target or "latest"
    if Path(target).is_dir():
        return Path(target).resolve()
    pool = _all_traces() if target == "latest" else sorted((J.REPO / "traces" / target).glob("*"))
    if not pool:
        J.fail(f"no traces for {target} (see: jxb trace list)")
    return pool[-1]


def cmd_plot(p, a) -> None:
    """Plots + report.md per recording (toolchain/jxb_plot.py, matplotlib in the dev container)."""
    dirs = [_resolve(t) for t in (a.targets or ["latest"])]
    for d in dirs:
        analyze(d)
    rc = J.in_container(p, shlex.join(["python3", "/opt/jxb/toolchain/jxb_plot.py", *map(str, dirs)]),
                        cwd=str(J.REPO))
    sys.exit(rc)


def cmd_compare(p, a) -> None:
    """Several recordings side by side (e.g. native vs container, ONNX Runtime vs TensorRT): traces/compare/<name>/."""
    dirs = [_resolve(t) for t in a.targets]
    for d in dirs:
        analyze(d)
    out = J.REPO / "traces" / "compare" / (a.name or dt.datetime.now().strftime("%Y%m%d-%H%M%S"))
    rc = J.in_container(p, shlex.join(["python3", "/opt/jxb/toolchain/jxb_plot.py", "--compare", str(out),
                                       *map(str, dirs)]), cwd=str(J.REPO))
    sys.exit(rc)


def cmd_list(p, a) -> None:
    rows = []
    for d in _all_traces():
        meta = json.loads((d / "meta.json").read_text()) if (d / "meta.json").exists() else {}
        rows.append({"dir": str(d), "board": d.parent.name, "trace": d.name, "kind": meta.get("kind"),
                     "label": meta.get("label"), "command": meta.get("command"), "wall_s": meta.get("wall_s"),
                     "rc": meta.get("rc")})
    if a.json:
        print(json.dumps(rows, indent=2))
        return
    for r in rows:
        print(f"{r['board']:12} {r['trace']:44} {str(r['wall_s']):>8} s  rc={r['rc']}  "
              f"{shlex.join(r['command'] or [])[:60]}")


# --------------------------------------------------------------------------------------------- parser
def register(sub, jxb) -> None:
    global J
    J = jxb
    t = sub.add_parser("trace", help="profile/trace on a board, analyze on the host",
                       description=__doc__, formatter_class=jxb.argparse.RawDescriptionHelpFormatter)
    ts = t.add_subparsers(dest="trace_cmd", required=True)

    def recording(name: str, help: str, fn) -> object:
        s = ts.add_parser(name, help=help)
        s.add_argument("name", help="board (boards.toml)")
        s.add_argument("--ws", help="workspace whose deployed install is sourced (default: current directory)")
        s.add_argument("--label", help="suffix of the trace directory")
        s.add_argument("--json", action="store_true", help="print summary.json instead of the text report")
        s.add_argument("--keep", action="store_true", help="keep the raw files on the board")
        s.add_argument("--interval", type=int, default=500, help="tegrastats interval in ms (default 500)")
        s.add_argument("--no-lock", action="store_true", help="don't wait for the board's measurement lock")
        s.set_defaults(fn=fn, cmd=[])
        return s

    s = recording("nsys", "Nsight Systems: jxb trace nsys NAME [options] -- CMD ...", cmd_nsys)
    s.add_argument("--duration", type=int, help="stop after S seconds (default: when CMD exits, or Ctrl-C)")
    s.add_argument("--delay", type=int, help="start collecting after S seconds")
    s.add_argument("--trace", default=NSYS_TRACE, help=f"APIs to trace (nsys --trace, default {NSYS_TRACE})")
    s.add_argument("--sample", choices=["process-tree", "none"], default="process-tree",
                   help="CPU sampling and context switches of the process tree")
    s.add_argument("--tegrastats", action="store_true", help="record tegrastats alongside")
    s.add_argument("--nsys-arg", action="append", default=[], help="extra nsys profile arguments (repeatable)")
    s = recording("tegrastats", "load, clocks, memory, temperatures, power [-- CMD ...]", cmd_tegrastats)
    s.add_argument("--duration", type=int, help="seconds (default 10 without CMD)")
    s.add_argument("--live", action="store_true", help="stream to the terminal instead of recording")
    s = recording("kernel", "ftrace: scheduler, IRQs, CPU frequency [-- CMD ...] (root on the board)", cmd_kernel)
    s.add_argument("--events", default="sched,irq,power",
                   help=f"comma list of presets ({', '.join(KERNEL_EVENTS)}) or tracefs events (sched/sched_switch)")
    s.add_argument("--duration", type=int, default=10, help="seconds, 0 = until Ctrl-C (default 10)")
    s.add_argument("--live", action="store_true", help="stream trace_pipe to the terminal")
    s.add_argument("--top", action="store_true", help="with --live: per-second scheduler statistics")
    s.add_argument("--root", choices=["sudo", "docker", "none"],
                   help="how to get root for tracefs (default: boards.toml trace_root, else sudo)")
    s.add_argument("--tegrastats", action="store_true", help="record tegrastats alongside")
    s = ts.add_parser("bench", help="inference benchmark matrix: ONNX Runtime EPs and TensorRT engines")
    s.add_argument("name", help="board (boards.toml)")
    s.add_argument("--models", nargs="+", required=True, metavar="ONNX", help="models to benchmark")
    s.add_argument("--backends", default=BENCH_BACKENDS, help=f"comma list (default {BENCH_BACKENDS})")
    s.add_argument("--iters", type=int, default=200, help="timed iterations per run (CPU: 10)")
    s.add_argument("--interval", type=int, default=500, help="tegrastats interval in ms (default 500)")
    s.add_argument("--label", help="suffix of the trace directory")
    s.add_argument("--json", action="store_true", help="print summary.json instead of the text report")
    s.add_argument("--keep", action="store_true", help="keep the raw results on the board")
    s.add_argument("--no-lock", action="store_true", help="don't wait for the board's measurement lock")
    s.set_defaults(fn=cmd_bench, cmd=[])
    s = ts.add_parser("gui", help="the board's Nsight GUI as a window here (X11 over SSH) [-- ARGS ...]")
    s.add_argument("name", help="board (boards.toml)")
    s.add_argument("--ws", help="workspace whose deployed install is sourced (default: current directory)")
    s.add_argument("--tool", choices=["nsys", "ncu"], default="nsys", help="Nsight Systems or Nsight Compute")
    s.set_defaults(fn=cmd_gui, cmd=[])
    s = ts.add_parser("report", help="analyze a recording: [DIR|BOARD|latest]")
    s.add_argument("target", nargs="?")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_report)
    s = ts.add_parser("plot", help="plots and report.md for recordings: [DIR|BOARD|latest ...]")
    s.add_argument("targets", nargs="*")
    s.set_defaults(fn=cmd_plot)
    s = ts.add_parser("compare", help="recordings side by side: DIR ... (traces/compare/<name>/)")
    s.add_argument("targets", nargs="+")
    s.add_argument("--name", help="name of the comparison (default: timestamp)")
    s.set_defaults(fn=cmd_compare)
    s = ts.add_parser("list", help="recordings in traces/")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_list)
