"""Perfetto timelines of jxb recordings: one or several recordings (several boards recorded at once with
`jxb ... NAME,NAME`, or runs to compare) on one time axis, in Chrome's JSON trace format, which
https://ui.perfetto.dev opens.

Each recording becomes a process group of tracks: its board state at the start; tegrastats counters (CPU, GPU,
RAM, board power, temperature); CPU and RSS of the session's processes; memory counters; topic rates; numeric bag
topics; the GPU kernels and NVTX ranges of an nsys recording. The time axis is this host's clock: a board's
timestamps (often without NTP) are mapped with the offset measured after the recording (meta.json "clock") and
the board's clock and uptime at its start (board_state_start.txt). Recordings without them (before 2026-11-09)
are placed by their host start time, to the second.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import sqlite3
from pathlib import Path

import jxb_trace

# tegrastats column, track, unit, scale. A counter's argument names its track's value: Perfetto shows "track unit".
TEGRASTATS_COUNTERS = (("cpu_avg_pct", "CPU", "%", 1.0), ("gpu_pct", "GPU", "%", 1.0),
                       ("ram_used_mb", "RAM used", "MB", 1.0), ("power_vdd_in_mw", "board power", "W", 1e-3),
                       ("temp_tj_c", "Tj", "C", 1.0))
MEMINFO_COUNTERS = ("MemAvailable", "Cached", "Shmem")
MAX_KERNELS = 200_000   # per recording; the JSON stays loadable


class Recording:
    """A recording's clock: board time and board uptime -> seconds on this host's clock."""

    def __init__(self, d: Path):
        self.dir = d
        self.meta = json.loads((d / "meta.json").read_text()) if (d / "meta.json").exists() else {}
        self.state = jxb_trace.board_state(d / "board_state_start.txt")
        clock = (self.state or {}).get("clock")
        self.offset = (self.meta.get("clock") or {}).get("offset_s")
        if clock and self.offset is not None:
            self.start = clock["wall_s"] - self.offset
            self.start_uptime = clock["uptime_s"]
            self.precise = True
        else:   # older recordings: the host's start time (seconds), board times relative to their first sample
            started = self.meta.get("started")
            self.start = dt.datetime.fromisoformat(started).timestamp() if started else d.stat().st_mtime
            self.start_uptime = None
            self.precise = False

    def from_board(self, t: float, first: float) -> float:
        """A board wall-clock time; first is the earliest such time of the series (fallback anchor)."""
        return t - self.offset if self.precise else self.start + (t - first)

    def from_uptime(self, u: float, first: float) -> float:
        return self.start + (u - self.start_uptime) if self.precise else self.start + (u - first)


def _first_uptime(log: Path) -> float | None:
    with open(log, errors="replace") as f:
        for line in f:
            if line.startswith("T "):
                return float(line.split()[1])
    return None


class Timeline:
    def __init__(self):
        self.events: list[dict] = []
        self.t0: float | None = None   # host seconds of the timeline's zero

    def us(self, t: float) -> float:
        return round((t - self.t0) * 1e6, 1)

    def counter(self, pid: int, name: str, unit: str, t: float, value: float) -> None:
        self.events.append({"ph": "C", "pid": pid, "name": name, "ts": self.us(t), "args": {unit: value}})

    def thread(self, pid: int, tid: int, name: str) -> None:
        self.events.append({"ph": "M", "pid": pid, "tid": tid, "name": "thread_name", "args": {"name": name}})


def _add_recording(tl: Timeline, pid: int, r: Recording) -> list[str]:
    """Events of one recording; returns what it contains."""
    d, m, have = r.dir, r.meta, []
    tl.events += [{"ph": "M", "pid": pid, "name": "process_name",
                   "args": {"name": f"{m.get('board', d.parent.name)}: {d.name}"}},
                  {"ph": "M", "pid": pid, "name": "process_sort_index", "args": {"sort_index": pid}}]
    args = {"command": " ".join(m.get("command") or []), "profile": m.get("profile"),
            "clock": "board clock mapped" if r.precise else "host start time (s)"}
    if r.state:
        args.update(jxb_trace.board_settings(r.state))
        args["fixed clocks"] = ", ".join(jxb_trace.fixed_clocks(r.state)) or "none"
    tl.events.append({"ph": "i", "s": "p", "pid": pid, "tid": 0, "name": "start: board state", "ts": tl.us(r.start),
                      "args": args})

    if (d / "tegrastats.csv").exists():   # sampled from the start, every interval
        interval = (m.get("interval_ms") or 500) / 1000
        with open(d / "tegrastats.csv") as f:
            for row in csv.DictReader(f):
                t = r.start + interval + float(row["t_s"])
                for key, name, unit, scale in TEGRASTATS_COUNTERS:
                    if row.get(key) not in (None, ""):
                        tl.counter(pid, name, unit, t, round(float(row[key]) * scale, 3))
        have.append("tegrastats")

    first = _first_uptime(d / "procstat.log") if (d / "procstat.log").exists() else None
    summary = json.loads((d / "summary.json").read_text()) if (d / "summary.json").exists() else {}
    if first is not None:
        for p in (summary.get("processes") or {}).get("session", []):
            for t_s, cpu, rss in p.get("series", []):
                t = r.from_uptime(first + t_s, first)
                tl.counter(pid, f"CPU {p['comm']} ({p['pid']})", "%", t, cpu)
                tl.counter(pid, f"RSS {p['comm']} ({p['pid']})", "MB", t, rss)
        if (d / "meminfo.csv").exists():
            with open(d / "meminfo.csv") as f:
                for row in csv.DictReader(f):
                    t = r.from_uptime(first + float(row["t_s"]), first)
                    for key in MEMINFO_COUNTERS:
                        if row.get(key) not in (None, ""):
                            tl.counter(pid, key, "MB", t, float(row[key]))
        have.append("processes")

    if (d / "rates.log").exists():
        rows = []
        for line in (d / "rates.log").read_text(errors="replace").splitlines():
            if line.startswith("{"):
                rows.append(json.loads(line))
        if rows:
            first_t = min(x["t"] for x in rows)
            for x in rows:
                tl.counter(pid, f"rate {x['probe']}", "Hz", r.from_board(x["t"], first_t), x["hz"])
            have.append("rates")

    for f in sorted((d / "bag/csv").glob("*.csv")) if (d / "bag/csv").is_dir() else []:
        with open(f) as fh:
            rows = [x for x in csv.DictReader(fh) if x.get("t_ns") and x.get("value") not in (None, "")]
        if rows:
            first_t = int(rows[0]["t_ns"]) / 1e9
            for x in rows:
                tl.counter(pid, f"bag {f.stem}", "value", r.from_board(int(x["t_ns"]) / 1e9, first_t),
                           float(x["value"]))
            have.append(f"bag {f.stem}")

    if (d / "report.sqlite").exists():
        have += _add_nsys(tl, pid, r, d / "report.sqlite")
    return have


def _add_nsys(tl: Timeline, pid: int, r: Recording, db: Path) -> list[str]:
    """GPU kernels per stream and NVTX ranges; nsys times count from its session start (board clock)."""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    tables = {x[0] for x in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    try:
        start_ns = con.execute("SELECT utcEpochNs FROM TARGET_INFO_SESSION_START_TIME").fetchone()[0]
    except sqlite3.Error:
        return []
    base = r.from_board(start_ns / 1e9, start_ns / 1e9)
    names = dict(con.execute("SELECT id, value FROM StringIds")) if "StringIds" in tables else {}
    have = []
    if "CUPTI_ACTIVITY_KIND_KERNEL" in tables:
        streams = set()
        rows = con.execute("SELECT start, end, shortName, streamId FROM CUPTI_ACTIVITY_KIND_KERNEL ORDER BY start "
                           f"LIMIT {MAX_KERNELS}").fetchall()
        for start, end, name, stream in rows:
            tid = 1000 + stream
            streams.add(stream)
            tl.events.append({"ph": "X", "pid": pid, "tid": tid, "name": names.get(name, str(name)),
                              "ts": tl.us(base + start / 1e9), "dur": round((end - start) / 1e3, 3)})
        for s in sorted(streams):
            tl.thread(pid, 1000 + s, f"GPU stream {s}")
        have.append(f"{len(rows)} GPU kernels")
    if "NVTX_EVENTS" in tables:
        rows = con.execute("SELECT start, end, text, textId FROM NVTX_EVENTS WHERE end IS NOT NULL").fetchall()
        for start, end, text, text_id in rows:
            tl.events.append({"ph": "X", "pid": pid, "tid": 999, "name": text or names.get(text_id, "range"),
                              "ts": tl.us(base + start / 1e9), "dur": round((end - start) / 1e3, 3)})
        if rows:
            tl.thread(pid, 999, "NVTX")
            have.append(f"{len(rows)} NVTX ranges")
    con.close()
    return have


def export(dirs: list[Path], out: Path) -> dict:
    """Writes the timeline of the recordings to out; returns what went in."""
    recs = [Recording(d) for d in dirs]
    tl = Timeline()
    tl.t0 = min(r.start for r in recs)
    contents = {}
    for i, r in enumerate(recs, 1):
        contents[str(r.dir)] = {"board_clock_mapped": r.precise, "starts_at_s": round(r.start - tl.t0, 3),
                                "contains": _add_recording(tl, i, r)}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"traceEvents": tl.events, "displayTimeUnit": "ms",
                               "metadata": {"t0": dt.datetime.fromtimestamp(tl.t0).astimezone().isoformat(),
                                            "recordings": contents}}))
    return contents
