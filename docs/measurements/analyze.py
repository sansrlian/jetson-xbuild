#!/usr/bin/env python3
"""Statistics, figures and LaTeX tables for the measurement report (docs/latex/measurements.tex).

  python3 docs/measurements/analyze.py [--traces traces/nano-lab]

Reads the raw recordings of the 2026-11-03 Orin Nano campaign (traces/nano-lab/, local and gitignored) with jxb's
own parsers (toolchain/jxb_trace.py) and writes
  docs/measurements/data/*.csv       derived datasets, one row per run / sample / kernel
  docs/measurements/stats.json       every number the report quotes
  docs/figures/measurements/*.pdf    the figures
  docs/latex/generated/*.tex         the tables
Needs numpy and matplotlib. Statistics: medians with 95 % moving-block bootstrap intervals (the samples of a run are
a time series, so blocks of 10 consecutive samples keep their autocorrelation), Cliff's delta as effect size.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "toolchain"))
import jxb_trace as T  # noqa: E402

FIG = REPO / "docs/figures/measurements"
DATA = REPO / "docs/measurements/data"
TEX = REPO / "docs/latex/generated"
RNG = np.random.default_rng(20261103)
WARMUP = 5            # inference samples dropped at the start of a run (first second at 5 Hz)
BLOCK, BOOT = 10, 4000
STATS: dict = {}

# --------------------------------------------------------------------------------- style (dataviz reference palette)
INK, INK2, MUTED, GRID, AXIS = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
SLOT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
IDLE_GRAY = "#c3c2b7"
W = 6.3                # \textwidth of the report in inches
plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 7.5, "axes.titlesize": 8, "axes.titleweight": "bold",
    "axes.titlelocation": "left", "axes.labelsize": 7.5, "xtick.labelsize": 7, "ytick.labelsize": 7,
    "legend.fontsize": 7, "axes.edgecolor": AXIS, "axes.linewidth": 0.6, "axes.labelcolor": INK2,
    "xtick.color": INK2, "ytick.color": INK2, "text.color": INK, "axes.titlecolor": INK,
    "xtick.major.size": 2.5, "ytick.major.size": 2.5, "xtick.major.width": 0.6, "ytick.major.width": 0.6,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.5, "axes.axisbelow": True,
    "axes.spines.top": False, "axes.spines.right": False, "lines.linewidth": 1.5,
    "lines.solid_capstyle": "round", "lines.solid_joinstyle": "round", "legend.frameon": False,
    "savefig.bbox": "tight", "savefig.pad_inches": 0.03, "pdf.fonttype": 42,
})
BACKEND_COLOR = {"ORT CPU": SLOT[3], "ORT CUDA EP": SLOT[0], "TensorRT fp32": SLOT[4], "TensorRT fp16": SLOT[1],
                 "TensorRT INT8": SLOT[2]}
ROLE_COLOR = {"image pipeline": SLOT[0], "detector": SLOT[1]}
CATEGORIES = ["image pipeline containers", "detector", "synthetic camera", "launch and measurement tools",
              "other nodes"]
CAT_COLOR = dict(zip(CATEGORIES, SLOT))


def save(fig, name: str) -> None:
    fig.savefig(FIG / f"{name}.pdf")
    plt.close(fig)


def tex_escape(s: str) -> str:
    return (s.replace("\\", r"\textbackslash{}").replace("_", r"\_").replace("%", r"\%").replace("&", r"\&")
            .replace("#", r"\#"))


def f(x, nd=1, dash="--") -> str:
    return dash if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.{nd}f}"


# ------------------------------------------------------------------------------------------------ statistics
def _block_idx(n: int) -> np.ndarray:
    L = min(BLOCK, n)
    k = math.ceil(n / L)
    starts = RNG.integers(0, n - L + 1, size=(BOOT, k))
    return (starts[:, :, None] + np.arange(L)[None, None, :]).reshape(BOOT, -1)[:, :n]


def median_ci(x) -> tuple[float, float]:
    x = np.asarray(x, float)
    if len(x) < 3:
        return math.nan, math.nan
    meds = np.median(x[_block_idx(len(x))], axis=1)
    lo, hi = np.percentile(meds, [2.5, 97.5])
    return float(lo), float(hi)


def compare(x, y) -> dict:
    """y relative to x: median difference and ratio with 95 % block-bootstrap intervals, Cliff's delta."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    mx, my = np.median(x[_block_idx(len(x))], axis=1), np.median(y[_block_idx(len(y))], axis=1)
    d, r = my - mx, my / mx
    cliff = (np.sum(y[:, None] > x[None, :]) - np.sum(y[:, None] < x[None, :])) / (len(x) * len(y))
    return {"median_x": float(np.median(x)), "median_y": float(np.median(y)),
            "diff": float(np.median(y) - np.median(x)), "diff_ci": [float(v) for v in np.percentile(d, [2.5, 97.5])],
            "ratio": float(np.median(y) / np.median(x)), "ratio_ci": [float(v) for v in np.percentile(r, [2.5, 97.5])],
            "cliffs_delta": float(cliff), "n_x": int(len(x)), "n_y": int(len(y))}


def slope_per_min(t, v) -> float:
    m = ~(np.isnan(t) | np.isnan(v))
    return float(np.polyfit(t[m], v[m], 1)[0] * 60) if m.sum() > 5 else math.nan


# --------------------------------------------------------------------------------------------- data access
class Run:
    def __init__(self, d: Path):
        self.d, self.name = d, d.name
        self.meta = json.loads((d / "meta.json").read_text()) if (d / "meta.json").exists() else {}
        self.time = f"{d.name[9:11]}:{d.name[11:13]}"
        self._teg = None

    @property
    def teg(self) -> dict[str, np.ndarray]:
        if self._teg is None:
            path = self.d / "tegrastats.csv"
            if path.exists():
                rows = list(csv.DictReader(open(path)))
                keys = rows[0].keys() if rows else []
                self._teg = {k: np.array([float(r[k]) if r.get(k) not in (None, "") else np.nan for r in rows])
                             for k in keys}
            else:   # recordings without analysis (the reset run): parse the raw log
                samples = [s for s in map(T.parse_tegrastats, (self.d / "tegrastats.log").read_text(
                    errors="replace").splitlines()) if s]
                keys = sorted({k for s in samples for k in s})
                self._teg = {k: np.array([s.get(k, np.nan) for s in samples], float) for k in keys}
                self._teg["t_s"] = np.arange(len(samples)) * 0.5
        return self._teg

    def teg_mean(self, key: str, t0: float = 5.0, t1: float | None = None) -> float:
        t, v = self.teg.get("t_s"), self.teg.get(key)
        if t is None or v is None:
            return math.nan
        t1 = (t.max() - 3.0) if t1 is None else t1
        m = (t >= t0) & (t <= t1)
        return float(np.nanmean(v[m])) if m.any() else math.nan

    def inference(self) -> tuple[np.ndarray, np.ndarray] | None:
        path = self.d / "bag/csv/object_detection__inference_time.csv"
        if not path.exists():
            return None
        rows = list(csv.DictReader(open(path)))
        t = np.array([int(r["t_ns"]) for r in rows], float) / 1e9
        return t - t[0], np.array([float(r["value"]) for r in rows])

    def procs(self) -> dict:
        return T.procstat_summary(self.d / "procstat.log", self.d / "sysconf", top=200)

    def probe_rates(self, i: int) -> np.ndarray:
        path = self.d / f"with{i}.log"
        if not path.exists():
            return np.array([])
        return np.array([float(m) for m in re.findall(r"average rate: ([\d.]+)", path.read_text(errors="replace"))])


def category(row: dict) -> str:
    c = row["comm"]
    if c.startswith("component_cont"):
        return CATEGORIES[0]
    if c.startswith("object_detecti"):
        return CATEGORIES[1]
    if c == "python3":
        return CATEGORIES[2] if row["alive_s"] > 10 else CATEGORIES[3]   # short-lived: jxb_bag_csv.py
    if c == "ros2":
        return CATEGORIES[3]
    return CATEGORIES[4]


# ------------------------------------------------------------------------------------------------- campaign
def campaign(traces: Path) -> list[dict]:
    """Every recording with its configuration and whether (and why) it enters the analysis."""
    notes = {
        "150743": "excluded: sampler without ancestry markers, concurrent HIL load counted (F53)",
        "195717": "excluded: no bag (topics passed as one argument)",
        "201949": "board reset during ORT CUDA EP initialization in the container (F56)",
        "203158": "launch died at start (foxglove\\_bridge); nsys waited 508 s for the process tree (F62)",
        "022216": "interrupted (rc 130); kernel data valid, span recomputed",
        "220718": "valid; GPU active 7.5--36 s, session open for 3800 s afterwards",
        "215928": "container needed SIGKILL at shutdown (rc 137); no bag",
        "220104": "valid; bag storage failed on Lyrical",
        "220202": "valid; bag storage failed on Lyrical",
        "151205": "valid (replicate); bag without CSV export", "202840": "valid (replicate after the lifecycle rework)",
        "204310": "invalid: image pipeline produced no output (diagnostics stale)",
        "220509": "valid; no bag, no rate probes",
        "021419": "3 samples only",
    }
    rows = []
    for d in sorted(p for p in traces.iterdir() if p.is_dir() and p.name.startswith("20261103")):
        r = Run(d)
        m = r.meta
        stamp = d.name[9:15]
        kind = m.get("kind") or d.name.split("-")[2]
        note = notes.get(stamp)
        if note is None:
            if m.get("rc") == 1 and kind == "launch":
                log = (d / "launch.log").read_text(errors="replace")
                note = ("failed start: topic\\_tools missing (F52)" if "topic_tools" in log else
                        "failed start: foxglove\\_bridge missing (F60)" if "foxglove_bridge" in log else
                        "failed start: osrf\\_pycommon missing (F61)" if ("osrf_pycommon" in log or "'launch'" in log)
                        else "failed start")
            else:
                note = "valid"
        label = "-".join(d.name.split("-")[3:]) or kind
        rows.append({"stamp": stamp, "time": r.time, "kind": kind, "label": label,
                     "profile": (m.get("profile") or "").replace("jp721-", ""), "mode": m.get("mode") or "--",
                     "wall_s": m.get("wall_s"), "rc": m.get("rc"), "note": note})
    with open(DATA / "campaign.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    lines = [r"\begin{longtable}{@{}ll>{\raggedright\arraybackslash}p{3.3cm}llrr>{\raggedright\arraybackslash}p{5.6cm}@{}}",
             r"\toprule",
             r"Time & Kind & Label & Profile & Mode & Wall [s] & rc & Status \\", r"\midrule", r"\endhead"]
    for r in rows:
        lines.append(" & ".join([r["time"], r["kind"], tex_escape(r["label"]), tex_escape(r["profile"] or "--"),
                                 r["mode"], f(r["wall_s"], 0) if r["wall_s"] is not None else "--",
                                 str(r["rc"]) if r["rc"] is not None else "--", r["note"]]) + r" \\")
    lines += [r"\bottomrule", r"\end{longtable}"]
    (TEX / "campaign.tex").write_text("\n".join(lines) + "\n")
    STATS["campaign"] = {"recordings": len(rows), "valid": sum(r["note"].startswith("valid") for r in rows),
                         "failed_starts": sum(r["note"].startswith("failed start") for r in rows)}
    return rows


# ---------------------------------------------------------------------------------------------- idle baseline
def idle(traces: Path) -> dict:
    """Idle board: the long tail of the stack's nsys recording (workload ended, GPU idle) plus the 3-sample check."""
    r = Run(find(traces, "220718-nsys-stack-parking-trt16-kilted"))
    t, gpu = r.teg["t_s"], r.teg["gpu_pct"]
    m = (t > 100) & (gpu == 0)
    out = {k: float(np.nanmean(r.teg[k][m])) for k in ("power_vdd_in_mw", "power_vdd_cpu_gpu_cv_mw",
                                                         "power_vdd_soc_mw", "cpu_avg_pct", "ram_used_mb",
                                                         "temp_tj_c") if k in r.teg}
    out["samples"] = int(m.sum())
    out["vdd_in_sd_mw"] = float(np.nanstd(r.teg["power_vdd_in_mw"][m]))
    q = Run(find(traces, "021419-tegrastats-idle"))
    out["check_vdd_in_mw"] = float(np.nanmean(q.teg["power_vdd_in_mw"]))
    STATS["idle"] = out
    return out


# --------------------------------------------------------------------------------------------------- benchmark
FAMILY = {"ort-cpu": "ORT CPU", "ort-cuda": "ORT CUDA EP", "ort-trt": "TensorRT fp32", "trtexec-fp32": "TensorRT fp32",
          "ort-trt-fp16": "TensorRT fp16", "trtexec-fp16": "TensorRT fp16", "ort-trt-int8": "TensorRT INT8",
          "trtexec-int8": "TensorRT INT8"}
MODEL = {"S0_fp32_ref-best": "S0 fp32", "P1_ptq_mo_max-best_int8": "P1 PTQ INT8", "S2_qat_finetune_s-best_int8":
         "S2 QAT INT8", "Q4_qat_s2recipe_fullint8-best_int8": "Q4 full INT8", "S3_qat_finetune_n-best_int8":
         "S3 QAT INT8 (n)"}
BACKEND = {"ort-cpu": "ORT CPU", "ort-cuda": "ORT CUDA EP", "ort-trt": "ORT TensorRT EP fp32",
           "ort-trt-fp16": "ORT TensorRT EP fp16", "ort-trt-int8": "ORT TensorRT EP INT8",
           "trtexec-fp32": "trtexec fp32", "trtexec-fp16": "trtexec fp16", "trtexec-int8": "trtexec INT8"}


def bench(traces: Path, base: dict) -> list[dict]:
    """Benchmark rows, each aligned with its tegrastats segment (the runs are sequential; a run's inference loop is
    the end of its segment) for power and energy per inference."""
    p_idle = base["power_vdd_in_mw"] / 1000
    out = []
    for suffix in ("151546-bench-s0-fp32", "152917-bench-int8"):
        r = Run(find(traces, suffix))
        rows = [json.loads(l) for l in (r.d / "results.jsonl").read_text().splitlines() if l.startswith("{")]
        start = 0.0
        total = sum(x["wall_s"] for x in rows)
        nominal = float(r.teg["t_s"].max() + 0.5)
        # tegrastats' real interval is slightly longer than the nominal 500 ms: stretch its time axis so that it
        # spans the runs' summed wall time (the runs are back to back)
        r.teg["t_s"] = r.teg["t_s"] * (total / nominal)
        STATS.setdefault("bench_alignment", {})[suffix] = {
            "sum_wall_s": round(total, 1), "tegrastats_nominal_s": nominal, "stretch": total / nominal}
        for x in rows:
            end = start + x["wall_s"]
            if x["backend"].startswith("trtexec"):
                log = (r.d / "logs" / f"{x['model']}.{x['backend']}.log")
                m = re.search(r"Total Host Walltime: ([\d.]+) s", log.read_text(errors="replace")) if log.exists() else None
                loop = float(m[1]) if m else 3.0
            else:
                warm = 2 if x["backend"] == "ort-cpu" else 20
                loop = (x["iters"] + warm) * x["mean_ms"] / 1000
            loop = min(loop, x["wall_s"])
            t0, t1 = max(start, end - loop + 0.5), end - 0.25     # skip the edges of the loop
            p = r.teg_mean("power_vdd_in_mw", t0, t1) / 1000
            gpu_pct = r.teg_mean("gpu_pct", t0, t1)
            # a window is usable if it holds several samples and, for GPU backends, shows the GPU busy
            ok = loop >= 2.5 and (x["backend"] == "ort-cpu" or gpu_pct >= 40)
            if not ok:
                p = math.nan
            row = {"model": MODEL.get(x["model"], x["model"]), "backend": BACKEND[x["backend"]],
                   "family": FAMILY[x["backend"]], "p50_ms": x["p50_ms"], "p90_ms": x.get("p90_ms"),
                   "p99_ms": x["p99_ms"], "fps": x["fps"], "gpu_p50_ms": x.get("gpu_p50_ms"),
                   "load_ms": x.get("load_ms"), "loop_s": round(loop, 2),
                   "power_w": p, "gpu_pct": gpu_pct, "window_ok": ok,
                   "cpu_pct": r.teg_mean("cpu_avg_pct", t0, t1),
                   "energy_mj": 1000 * p / x["fps"], "energy_dyn_mj": 1000 * (p - p_idle) / x["fps"]}
            out.append(row)
            start = end
    with open(DATA / "bench.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out[0]))
        w.writeheader()
        w.writerows(out)
    lines = [r"\begin{tabular}{@{}llrrrrrrr@{}}", r"\toprule",
             r"Model & Backend & p50 & p99 & Rate & $\bar P$ & GPU & $E_{\mathrm{inf}}$ & $E_{\mathrm{dyn}}$ \\",
             r" & & [ms] & [ms] & [1/s] & [W] & [\%] & [mJ] & [mJ] \\", r"\midrule"]
    last = None
    for x in out:
        if last and x["model"] != last:
            lines.append(r"\addlinespace")
        lines.append(" & ".join([x["model"] if x["model"] != last else "", x["backend"], f(x["p50_ms"], 2),
                                 f(x["p99_ms"], 2), f(x["fps"], 1), f(x["power_w"], 2), f(x["gpu_pct"], 0),
                                 f(x["energy_mj"], 0), f(x["energy_dyn_mj"], 0)]) + r" \\")
        last = x["model"]
    lines += [r"\bottomrule", r"\end{tabular}"]
    (TEX / "bench.tex").write_text("\n".join(lines) + "\n")
    STATS["bench"] = out
    return out


def fig_bench(rows: list[dict]) -> None:
    order = [x for x in rows if x["model"] == "S0 fp32"] + [x for x in rows if x["model"] != "S0 fp32"]
    fig, ax = plt.subplots(figsize=(W, 4.3))
    for i, x in enumerate(order):
        c = BACKEND_COLOR[x["family"]]
        trt = x["backend"].startswith("trtexec")
        ax.plot([x["p50_ms"], x["p99_ms"]], [i, i], color=c, lw=1.2, alpha=0.55, zorder=2)
        if x["p90_ms"]:
            ax.plot([x["p90_ms"]] * 2, [i - 0.22, i + 0.22], color=c, lw=1.0, zorder=2)
        ax.scatter([x["p50_ms"]], [i], s=26, marker="D" if trt else "o", color=c, edgecolor="white", linewidth=0.9,
                   zorder=3)
        ax.text(x["p99_ms"] * 1.1, i, f"{x['p50_ms']:.1f}", va="center", fontsize=6.3, color=INK2)
    ax.set_yticks(np.arange(len(order)), [f"{x['model']}:  {x['backend']}" for x in order])
    ax.invert_yaxis()
    ax.set_xscale("log")
    ax.set_xlim(4, 1000)
    ax.set_xlabel("latency per inference [ms], log scale (dot: p50, tick: p90, line end: p99; label: p50)")
    ax.grid(axis="y", visible=False)
    ax.axhline(sum(1 for x in order if x["model"] == "S0 fp32") - 0.5, color=AXIS, lw=0.6)
    handles = [Line2D([], [], color=c, marker="o", lw=0, markersize=5, label=k) for k, c in BACKEND_COLOR.items()]
    handles += [Line2D([], [], color=MUTED, marker="o", lw=0, markersize=4.5, label="ONNX Runtime"),
                Line2D([], [], color=MUTED, marker="D", lw=0, markersize=4.5, label="trtexec")]
    ax.legend(handles=handles, loc="lower center", ncol=7, bbox_to_anchor=(0.42, 1.0), fontsize=6.5,
              handletextpad=0.2, columnspacing=1.0)
    save(fig, "bench_latency")

    gpu = [x for x in order if x["family"] != "ORT CPU" and x["window_ok"]]
    fig, ax = plt.subplots(figsize=(W, 2.9))
    yy = np.arange(len(gpu))
    idle_part = [x["energy_mj"] - x["energy_dyn_mj"] for x in gpu]
    ax.barh(yy, idle_part, height=0.62, color=IDLE_GRAY, edgecolor="white", linewidth=1.0)
    ax.barh(yy, [x["energy_dyn_mj"] for x in gpu], left=idle_part, height=0.62,
            color=[BACKEND_COLOR[x["family"]] for x in gpu], edgecolor="white", linewidth=1.0)
    for i, x in enumerate(gpu):
        ax.text(x["energy_mj"] + 6, i, f"{x['energy_mj']:.0f} mJ  ({x['power_w']:.1f} W, {x['fps']:.0f}/s)",
                va="center", fontsize=6.3, color=INK2)
    ax.set_yticks(yy, [f"{x['model']}:  {x['backend']}" for x in gpu])
    ax.invert_yaxis()
    ax.set_xlabel("energy per inference at the board input [mJ] = mean power / throughput")
    ax.grid(axis="y", visible=False)
    ax.set_xlim(0, max(x["energy_mj"] for x in gpu) * 1.45)
    handles = [Patch(color=IDLE_GRAY, label="share of the idle power")]
    handles += [Patch(color=BACKEND_COLOR[k], label=f"above idle: {k}") for k in ("ORT CUDA EP", "TensorRT fp32",
                                                                                 "TensorRT fp16", "TensorRT INT8")]
    ax.legend(handles=handles, loc="lower center", ncol=5, bbox_to_anchor=(0.4, 1.0), fontsize=6.3,
              handletextpad=0.3, columnspacing=0.9)
    save(fig, "bench_energy")


# ----------------------------------------------------------------------------------------------- detector runs
DETECTOR = [  # suffix, backend, distro, mode, short label
    ("200054-launch-od-onnx-jazzy-native", "ORT CUDA EP", "Jazzy", "native", "Jazzy ORT CUDA EP"),
    ("151205-launch-od-ort-jazzy-native", "ORT CUDA EP", "Jazzy", "native", "Jazzy ORT CUDA EP (rep.)"),
    ("200152-launch-od-trt-fp16-jazzy-native", "TensorRT fp16", "Jazzy", "native", "Jazzy TRT fp16"),
    ("200249-launch-od-trt-int8-qat-jazzy-native", "TensorRT INT8", "Jazzy", "native", "Jazzy TRT INT8 (QAT)"),
    ("201036-launch-od-onnx-kilted-native", "ORT CUDA EP", "Kilted", "native", "Kilted ORT CUDA EP"),
    ("201137-launch-od-trt-fp16-kilted-native", "TensorRT fp16", "Kilted", "native", "Kilted TRT fp16"),
    ("202840-launch-od-v2check-kilted-native", "TensorRT fp16", "Kilted", "native", "Kilted TRT fp16 (rep.)"),
    ("202324-launch-od-trt-fp16-kilted-container", "TensorRT fp16", "Kilted", "container", "Kilted TRT fp16, container"),
    ("220104-launch-od-onnx-lyrical-isaac-native", "ORT CUDA EP", "Lyrical", "native", "Lyrical ORT CUDA EP"),
    ("220202-launch-od-trt-fp16-lyrical-isaac-native", "TensorRT fp16", "Lyrical", "native", "Lyrical TRT fp16"),
    ("215928-launch-od-trt-fp16-lyrical-container", "TensorRT fp16", "Lyrical 26.04", "container",
     "Lyrical 26.04 TRT fp16, container"),
]


def detector(traces: Path, base: dict) -> dict:
    p_idle = base["power_vdd_in_mw"] / 1000
    runs, samples = {}, []
    for suffix, backend, distro, mode, label in DETECTOR:
        r = Run(find(traces, suffix))
        ps = r.procs()
        det = next((x for x in ps["session"] if x["comm"].startswith("object_detecti")), None)
        cam = next((x for x in ps["session"] if x["comm"] == "python3" and x["alive_s"] > 10), None)
        inf = r.inference()
        row = {"label": label, "backend": backend, "distro": distro, "mode": mode, "time": r.time,
               "session_cpu": ps["session_cpu_pct"] / 100, "session_rss_gb": ps["session_rss_mb"] / 1024,
               "detector_cpu": det["cpu_share_pct"] / 100 if det else math.nan,
               "detector_rss_gb": det["rss_max_mb"] / 1024 if det else math.nan,
               "camera_cpu": cam["cpu_share_pct"] / 100 if cam else math.nan,
               "gpu_pct": r.teg_mean("gpu_pct"), "power_w": r.teg_mean("power_vdd_in_mw") / 1000,
               "cpu_gpu_cv_w": r.teg_mean("power_vdd_cpu_gpu_cv_mw") / 1000,
               "soc_w": r.teg_mean("power_vdd_soc_mw") / 1000, "temp_max_c": float(np.nanmax(r.teg["temp_tj_c"])),
               "monitor_cpu": ps["monitor_cpu_pct"] / 100}
        if inf is not None:
            t, v = inf
            first = float(v[0])
            t, v = t[WARMUP:], v[WARMUP:]
            lo, hi = median_ci(v)
            iat = np.diff(t)
            row.update(n=len(v), first_ms=first, median_ms=float(np.median(v)), ci_lo=lo, ci_hi=hi,
                       p90_ms=float(np.percentile(v, 90)), p99_ms=float(np.percentile(v, 99)),
                       mean_ms=float(np.mean(v)), rate_hz=float(1 / np.mean(iat)), iat_sd_ms=float(np.std(iat) * 1000))
            row["energy_det_j"] = row["power_w"] / row["rate_hz"]
            row["energy_det_dyn_j"] = (row["power_w"] - p_idle) / row["rate_hz"]
            samples += [{"label": label, "t_s": round(float(a), 3), "inference_ms": round(float(b), 3)}
                        for a, b in zip(t, v)]
            runs[label] = (row, v)
        else:
            runs[label] = (row, None)
    with open(DATA / "detector_runs.csv", "w", newline="") as fh:
        keys = list(dict.fromkeys(k for row, _ in runs.values() for k in row))
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(row for row, _ in runs.values())
    with open(DATA / "detector_samples.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["label", "t_s", "inference_ms"])
        w.writeheader()
        w.writerows(samples)
    # table
    lines = [r"\begin{tabular}{@{}lrrrrrrrrr@{}}", r"\toprule",
             r"Configuration & $n$ & Median [ms] & p99 & Rate & Det.\ CPU & Det.\ RSS & GPU & $\bar P$ & $E_{\mathrm{det}}$ \\",
             r" & & (95\,\% CI) & [ms] & [Hz] & [cores] & [GB] & [\%] & [W] & [J] \\", r"\midrule"]
    for row, v in runs.values():
        med = (f"{row['median_ms']:.2f} ({row['ci_lo']:.2f}--{row['ci_hi']:.2f})" if v is not None else "--")
        lines.append(" & ".join([tex_escape(row["label"]), str(row.get("n", "--")), med, f(row.get("p99_ms"), 1),
                                 f(row.get("rate_hz"), 2), f(row["detector_cpu"], 2), f(row["detector_rss_gb"], 2),
                                 f(row["gpu_pct"], 1), f(row["power_w"], 2), f(row.get("energy_det_j"), 2)]) + r" \\")
    lines += [r"\bottomrule", r"\end{tabular}"]
    (TEX / "detector.tex").write_text("\n".join(lines) + "\n")
    STATS["detector"] = {k: row for k, (row, _) in runs.items()}
    return runs


def fig_detector(runs: dict, stack_inf: dict) -> None:
    def ecdf(ax, items, title, xlim):
        for label, v, c in items:
            x = np.sort(v)
            ax.step(x, np.arange(1, len(x) + 1) / len(x), where="post", color=c, lw=1.5, label=label)
        ax.set_xlim(*xlim)
        ax.set_ylim(0, 1.02)
        ax.set_title(title)
        ax.set_xlabel("inference time [ms]")
        ax.legend(loc="lower right", fontsize=6.3, handlelength=1.4)

    fig, axs = plt.subplots(2, 2, figsize=(W, 4.4), gridspec_kw={"hspace": 0.55, "wspace": 0.22})
    v = {k: runs[k][1] for k in runs}
    ecdf(axs[0, 0], [("ORT CUDA EP", v["Jazzy ORT CUDA EP"], BACKEND_COLOR["ORT CUDA EP"]),
                     ("TensorRT fp16", v["Jazzy TRT fp16"], BACKEND_COLOR["TensorRT fp16"]),
                     ("TensorRT INT8 (QAT)", v["Jazzy TRT INT8 (QAT)"], BACKEND_COLOR["TensorRT INT8"])],
         "(a) Backend (Jazzy, native)", (10, 80))
    ecdf(axs[0, 1], [("Jazzy, native", v["Jazzy TRT fp16"], SLOT[0]), ("Kilted, native", v["Kilted TRT fp16"], SLOT[1]),
                     ("Kilted, container", v["Kilted TRT fp16, container"], SLOT[2]),
                     ("Kilted, native, replicate", v["Kilted TRT fp16 (rep.)"], SLOT[3])],
         "(b) TensorRT fp16: distribution, mode, replicate", (16.5, 19))
    ecdf(axs[1, 0], [("alone", v["Kilted TRT fp16"], SLOT[0]), ("in the stack", stack_inf["TensorRT fp16"], SLOT[1])],
         "(c) TensorRT fp16: alone vs. in the stack", (14, 45))
    ecdf(axs[1, 1], [("alone", v["Kilted ORT CUDA EP"], SLOT[0]), ("in the stack", stack_inf["ORT CUDA EP"], SLOT[1])],
         "(d) ORT CUDA EP: alone vs. in the stack", (35, 110))
    for ax in axs[:, 0]:
        ax.set_ylabel("fraction of inferences")
    save(fig, "detector_ecdf")

    rows = [r for r, _ in runs.values()]
    fig, axs = plt.subplots(1, 4, figsize=(W, 2.9), sharey=True, gridspec_kw={"wspace": 0.12})
    y = np.arange(len(rows))
    cols = [BACKEND_COLOR[r["backend"]] for r in rows]
    for ax, key, xl, nd in zip(axs, ["detector_cpu", "detector_rss_gb", "gpu_pct", "power_w"],
                               ["detector CPU [cores]", "detector RSS [GB]", "GPU load, mean [%]", "board power [W]"],
                               [2, 2, 1, 2]):
        vals = [r[key] for r in rows]
        ax.barh(y, vals, height=0.58, color=cols, edgecolor="white", linewidth=1.0)
        for i, val in enumerate(vals):
            ax.text(val * 1.02 + 0.01 * max(vals), i, f"{val:.{nd}f}", va="center", fontsize=6, color=INK2)
        ax.set_xlim(0, max(vals) * 1.32)
        ax.set_xlabel(xl)
        ax.grid(axis="y", visible=False)
    axs[0].set_yticks(y, [r["label"] for r in rows], fontsize=6.5)
    axs[0].invert_yaxis()
    fig.legend(handles=[Patch(color=BACKEND_COLOR[k], label=k) for k in ("ORT CUDA EP", "TensorRT fp16", "TensorRT INT8")],
               loc="upper center", ncol=3, bbox_to_anchor=(0.55, 1.03), fontsize=6.8)
    save(fig, "detector_resources")


# -------------------------------------------------------------------------------------------------- full stack
STACK = [  # suffix, backend, mode, label
    ("220300-launch-stack-parking-onnx-kilted-native", "ORT CUDA EP", "native", "ORT CUDA EP, native"),
    ("204528-launch-stack-parking-trt-fp16-kilted-native", "TensorRT fp16", "native", "TRT fp16, native"),
    ("205429-launch-stack-parking-trt-fp16-kilted-container", "TensorRT fp16", "container", "TRT fp16, container"),
    ("220509-launch-stack-parking-trt-fp16-kilted-native-nodes", "TensorRT fp16", "native",
     "TRT fp16, native, per node*"),
]


def stack(traces: Path, base: dict) -> tuple[dict, dict]:
    p_idle = base["power_vdd_in_mw"] / 1000
    out, stack_inf, proc_rows = {}, {}, []
    for suffix, backend, mode, label in STACK:
        r = Run(find(traces, suffix))
        ps = r.procs()
        cats = defaultdict(lambda: [0.0, 0.0, 0])
        for x in ps["session"]:
            c = category(x)
            cats[c][0] += x["cpu_share_pct"] / 100
            cats[c][1] += x["rss_max_mb"] / 1024
            cats[c][2] += 1
            proc_rows.append({"run": label, "pid": x["pid"], "comm": x["comm"], "category": c,
                              "cpu_share_cores": x["cpu_share_pct"] / 100, "cpu_max_cores": x["cpu_max_pct"] / 100,
                              "rss_max_mb": x["rss_max_mb"], "alive_s": x["alive_s"]})
        # memory over time: system RAM (tegrastats) vs. the session's summed RSS (1 s samples)
        rss_t = defaultdict(float)
        for x in ps["session"]:
            for t, _cpu, rss in x.get("series", []):
                rss_t[t] += rss
        ts = np.array(sorted(rss_t))
        rs = np.array([rss_t[k] for k in ts])
        t, ram = r.teg["t_s"], r.teg["ram_used_mb"]
        steady = (t > 20) & (t < t.max() - 10)
        rsteady = (ts > 20) & (ts < ts.max() - 10)
        inf = r.inference()
        meta_bag = r.d / "bag/metadata.yaml"
        topics = {x["topic"]: x for x in T.bag_summary(meta_bag)["topics"]} if meta_bag.exists() else {}
        row = {"label": label, "backend": backend, "mode": mode, "time": r.time,
               "processes": len(T.launch_log_summary(r.d / "launch.log")["processes"]),
               "clean": T.launch_log_summary(r.d / "launch.log")["finished_cleanly"],
               "session_cpu": ps["session_cpu_pct"] / 100, "session_rss_gb": ps["session_rss_mb"] / 1024,
               "cpu_pct": r.teg_mean("cpu_avg_pct", 10), "gpu_pct": r.teg_mean("gpu_pct", 10),
               "power_w": r.teg_mean("power_vdd_in_mw", 10) / 1000,
               "cpu_gpu_cv_w": r.teg_mean("power_vdd_cpu_gpu_cv_mw", 10) / 1000,
               "soc_w": r.teg_mean("power_vdd_soc_mw", 10) / 1000,
               "dyn_power_w": r.teg_mean("power_vdd_in_mw", 10) / 1000 - p_idle,
               "temp_max_c": float(np.nanmax(r.teg["temp_tj_c"])), "ram_mean_gb": r.teg_mean("ram_used_mb", 20) / 1024,
               "ram_slope_mb_min": slope_per_min(t[steady], ram[steady]),
               "rss_slope_mb_min": slope_per_min(ts[rsteady].astype(float), rs[rsteady]),
               "lane_hz": topics.get("/center_point_detection/center_points", {}).get("hz", math.nan),
               "undist_hz_probe": float(np.median(r.probe_rates(1))) if r.probe_rates(1).size else math.nan,
               "det_img_hz_probe": float(np.median(r.probe_rates(2))) if r.probe_rates(2).size else math.nan,
               "categories": {k: {"cpu": v[0], "rss_gb": v[1], "n": v[2]} for k, v in cats.items()}}
        if inf is not None:
            v = inf[1][WARMUP:]
            row.update(inf_median=float(np.median(v)), inf_p90=float(np.percentile(v, 90)),
                       inf_p99=float(np.percentile(v, 99)), inf_n=len(v))
            row["inf_ci"] = median_ci(v)
            stack_inf.setdefault(backend, v)
        row["_series"] = {"t": t, "ram": ram, "rss_t": ts, "rss": rs, "teg": r.teg,
                          "probe1": r.probe_rates(1), "probe2": r.probe_rates(2)}
        out[label] = row
    with open(DATA / "stack_processes.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(proc_rows[0]))
        w.writeheader()
        w.writerows(proc_rows)
    keys = [k for k in next(iter(out.values())) if not k.startswith("_") and k != "categories"]
    with open(DATA / "stack_runs.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        w.writerows(out.values())
    lines = [r"\begin{tabular}{@{}lrrrrrrrrr@{}}", r"\toprule",
             r"Configuration & CPU & RSS & GPU & $\bar P$ & $P_{\mathrm{dyn}}$ & Lanes & Undist. & Inference & RAM slope \\",
             r" & [cores] & [GB] & [\%] & [W] & [W] & [Hz] & [Hz] & p50/p99 [ms] & [MB/min] \\", r"\midrule"]
    for row in out.values():
        inf = f"{row['inf_median']:.1f} / {row['inf_p99']:.1f}" if "inf_median" in row else "--"
        lines.append(" & ".join([tex_escape(row["label"]), f(row["session_cpu"], 2), f(row["session_rss_gb"], 2),
                                 f(row["gpu_pct"], 1), f(row["power_w"], 2), f(row["dyn_power_w"], 2),
                                 f(row["lane_hz"], 1), f(row["undist_hz_probe"], 1), inf,
                                 f(row["ram_slope_mb_min"], 0)]) + r" \\")
    lines += [r"\bottomrule", r"\end{tabular}"]
    (TEX / "stack.tex").write_text("\n".join(lines) + "\n")
    STATS["stack"] = {k: {kk: vv for kk, vv in v.items() if not kk.startswith("_")} for k, v in out.items()}
    return out, stack_inf


def fig_stack(st: dict) -> None:
    labels = list(st)
    fig, axs = plt.subplots(1, 2, figsize=(W, 2.5), sharey=True, gridspec_kw={"wspace": 0.08})
    y = np.arange(len(labels))
    for ax, key, xl in zip(axs, ["cpu", "rss_gb"], ["CPU share of the recording [cores]", "peak RSS [GB]"]):
        left = np.zeros(len(labels))
        for c in CATEGORIES:
            vals = np.array([st[l]["categories"].get(c, {}).get(key, 0.0) for l in labels])
            ax.barh(y, vals, left=left, height=0.6, color=CAT_COLOR[c], edgecolor="white", linewidth=1.0, label=c)
            left += vals
        for i, tot in enumerate(left):
            ax.text(tot + 0.02 * left.max(), i, f"{tot:.2f}", va="center", fontsize=6.3, color=INK2)
        ax.set_xlim(0, left.max() * 1.18)
        ax.set_xlabel(xl)
        ax.grid(axis="y", visible=False)
    axs[0].set_yticks(y, labels, fontsize=6.8)
    axs[0].invert_yaxis()
    handles, names = axs[0].get_legend_handles_labels()
    fig.legend(handles, names, loc="upper center", ncol=3, bbox_to_anchor=(0.52, 1.13), fontsize=6.6)
    save(fig, "stack_breakdown")

    # time series: native vs container (TensorRT) and native ONNX Runtime
    sel = [("TRT fp16, native", SLOT[0]), ("TRT fp16, container", SLOT[1]), ("ORT CUDA EP, native", SLOT[2])]
    fig, axs = plt.subplots(4, 1, figsize=(W, 4.6), sharex=True, gridspec_kw={"hspace": 0.28})
    for key, ax, yl, scale in zip(["cpu_avg_pct", "gpu_pct", "ram_used_mb", "power_vdd_in_mw"], axs,
                                  ["CPU, mean of cores [%]", "GPU load [%]", "RAM used [GB]", "board power [W]"],
                                  [1, 1, 1 / 1024, 1 / 1000]):
        for label, c in sel:
            s = st[label]["_series"]["teg"]
            v = s[key] * scale
            k = 5 if key in ("gpu_pct", "cpu_avg_pct", "power_vdd_in_mw") else 1
            sm = np.convolve(v, np.ones(k) / k, mode="same") if k > 1 else v
            ax.plot(s["t_s"], sm, color=c, lw=1.2, label=label)
        ax.set_ylabel(yl, fontsize=6.8)
    axs[0].legend(loc="upper center", ncol=3, bbox_to_anchor=(0.5, 1.42), fontsize=6.8)
    axs[-1].set_xlabel("time since the start of the recording [s] (CPU, GPU, power: 2.5 s moving mean)")
    save(fig, "stack_timeseries")

    # memory: system RAM vs. the session's RSS
    fig, axs = plt.subplots(1, 2, figsize=(W, 2.3), sharey=True, gridspec_kw={"wspace": 0.08})
    for ax, label, title in zip(axs, ["TRT fp16, native", "TRT fp16, native, per node*"],
                                ["(a) with bag recorder and rate probes", "(b) per node, no bag, no probes"]):
        s = st[label]["_series"]
        ax.plot(s["t"], s["ram"] / 1024, color=SLOT[0], label="system RAM used (tegrastats)")
        ax.plot(s["rss_t"], s["rss"] / 1024, color=SLOT[1], label="session processes, summed RSS")
        ax.set_title(title)
        ax.set_xlabel("time [s]")
        ax.text(0.98, 0.06, f"RAM {st[label]['ram_slope_mb_min']:+.0f} MB/min\nRSS {st[label]['rss_slope_mb_min']:+.0f} MB/min",
                transform=ax.transAxes, ha="right", va="bottom", fontsize=6.5, color=INK2)
    axs[0].set_ylabel("memory [GB]")
    axs[0].legend(loc="upper left", fontsize=6.4)
    save(fig, "stack_memory")

    # rates measured by the probes
    fig, axs = plt.subplots(1, 2, figsize=(W, 2.2), gridspec_kw={"wspace": 0.25})
    for label, c in sel:
        s = st[label]["_series"]
        axs[0].plot(np.arange(len(s["probe1"])), s["probe1"], color=c, lw=1.0, label=label)
        axs[1].plot(np.arange(len(s["probe2"])), s["probe2"], color=c, lw=1.0, label=label)
    axs[0].axhline(30, color=INK2, lw=0.8, ls=(0, (2, 2)))
    axs[0].text(1, 30.6, "camera input 30 Hz", fontsize=6.3, color=INK2)
    axs[0].set_ylim(0, 34)
    axs[0].set_title("(a) undistorted images")
    axs[1].set_ylim(0, 8)
    axs[1].set_title("(b) detection images")
    for ax in axs:
        ax.set_xlabel("probe report (about one per second)")
        ax.set_ylabel("rate [Hz]")
    axs[0].legend(loc="lower right", fontsize=6.2)
    save(fig, "stack_rates")


# ------------------------------------------------------------------------------------------- follow-up: H7 memory test
def memtest(traces: Path) -> None:
    """The 25-minute stack run of 2026-11-04 (no bag, no probes, /proc/meminfo sampled): does memory keep growing?"""
    r = Run(find(traces, "launch-stack-memtest-trt-fp16-kilted-native"))
    stretch = float(np.mean([v["stretch"] for v in STATS["bench_alignment"].values()]))
    t_teg = r.teg["t_s"] * stretch
    mem = list(csv.DictReader(open(r.d / "meminfo.csv")))
    t_mem = np.array([float(x["t_s"]) for x in mem])
    col = {k: np.array([float(x[k]) if x.get(k) else np.nan for x in mem]) for k in ("MemAvailable", "Cached", "MemFree")}
    ps = r.procs()
    rss_t = defaultdict(float)
    for x in ps["session"]:
        for tt, _cpu, rss in x.get("series", []):
            rss_t[tt] += rss
    ts = np.array(sorted(rss_t))
    rs = np.array([rss_t[k] for k in ts])

    def slope(t, v, a, b):
        m = (t >= a) & (t <= b) & ~np.isnan(v)
        return float(np.polyfit(t[m], v[m], 1)[0] * 60)
    phases = [(15, 120), (120, 300), (300, 600), (600, 1490)]
    out = {"duration_s": float(t_mem.max()), "processes": len(T.launch_log_summary(r.d / "launch.log")["processes"]),
           "clean": T.launch_log_summary(r.d / "launch.log")["finished_cleanly"],
           "session_cpu": ps["session_cpu_pct"] / 100, "session_rss_gb": ps["session_rss_mb"] / 1024,
           "power_w": r.teg_mean("power_vdd_in_mw", 10) / 1000, "gpu_pct": r.teg_mean("gpu_pct", 10),
           "temp_max_c": float(np.nanmax(r.teg["temp_tj_c"])), "guard_fired": (r.d / "memguard.log").exists(),
           "phases": [{"from_s": a, "to_s": b, "ram_used_mb_min": slope(t_teg, r.teg["ram_used_mb"], a, b),
                       "available_mb_min": slope(t_mem, col["MemAvailable"], a, b),
                       "cached_mb_min": slope(t_mem, col["Cached"], a, b),
                       "rss_mb_min": slope(ts, rs, max(a, 20), b)} for a, b in phases],
           "plateau_ram_used_mb": float(np.nanmean(r.teg["ram_used_mb"][(t_teg >= 600) & (t_teg <= 1490)])),
           "plateau_available_mb": float(np.nanmean(col["MemAvailable"][(t_mem >= 600) & (t_mem <= 1490)])),
           "start_available_mb": float(col["MemAvailable"][0]), "start_ram_used_mb": float(r.teg["ram_used_mb"][0]),
           "rss_plateau_mb": float(np.mean(rs[(ts >= 60) & (ts <= 1490)]))}
    STATS["memtest"] = out
    with open(DATA / "memtest_series.csv", "w", newline="") as fh:   # every 10 s, for the dashboard
        w = csv.writer(fh)
        w.writerow(["t_min", "used_gb", "rss_gb", "cached_gb", "available_gb"])
        for tt in np.arange(0, t_mem.max() + 1e-9, 10.0):
            near = lambda tv: int(np.argmin(np.abs(tv - tt)))
            w.writerow([round(tt / 60, 3), round(r.teg["ram_used_mb"][near(t_teg)] / 1024, 3),
                        round(rs[near(ts)] / 1024, 3) if len(ts) else "", round(col["Cached"][near(t_mem)] / 1024, 3),
                        round(col["MemAvailable"][near(t_mem)] / 1024, 3)])
    fig, axs = plt.subplots(1, 2, figsize=(W, 2.5), gridspec_kw={"wspace": 0.12, "width_ratios": [1.6, 1]}, sharey=True)
    for ax, lim in zip(axs, (None, 300)):
        ax.plot(t_teg / 60, r.teg["ram_used_mb"] / 1024, color=SLOT[0], label="used memory (tegrastats)")
        ax.plot(ts / 60, rs / 1024, color=SLOT[1], label="session processes, summed RSS")
        ax.plot(t_mem / 60, col["Cached"] / 1024, color=SLOT[2], label="page cache (Cached)")
        ax.plot(t_mem / 60, col["MemAvailable"] / 1024, color=SLOT[3], label="MemAvailable")
        ax.set_xlabel("time since the start of the recording [min]")
        if lim:
            ax.set_xlim(0, lim / 60)
            ax.set_title("(b) first 5 minutes")
    axs[0].set_title("(a) 25-minute run")
    axs[0].set_ylabel("memory [GB]")
    axs[0].set_ylim(0, 7)
    handles, labels = axs[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncol=4, bbox_to_anchor=(0.5, 1.1), fontsize=6.6)
    save(fig, "memtest")


# ------------------------------------------------------------------------------------------------ Nsight Systems
def nsys(traces: Path) -> dict:
    res = {}
    for suffix, key in (("021327-nsys-ort-cuda", "ort_cuda"), ("022216-nsys-ort-trt16", "ort_trt16"),
                        ("220718-nsys-stack-parking-trt16-kilted", "stack")):
        d = find(traces, suffix)
        con = sqlite3.connect(f"file:{d / 'report.sqlite'}?mode=ro", uri=True)
        name_of = {}
        for gp, pid, name in con.execute("SELECT globalPid, pid, name FROM PROCESSES"):
            name_of[gp] = name_of[pid] = name
        kern = con.execute("SELECT k.start, k.end, k.globalPid, s.value FROM CUPTI_ACTIVITY_KIND_KERNEL k "
                           "JOIN StringIds s ON s.id = k.shortName ORDER BY k.start").fetchall()
        mem = con.execute("SELECT copyKind, bytes, end - start, globalPid FROM CUPTI_ACTIVITY_KIND_MEMCPY").fetchall()
        api = con.execute("SELECT s.value, r.end - r.start, (r.globalTid >> 24) & 16777215 FROM "
                          "CUPTI_ACTIVITY_KIND_RUNTIME r JOIN StringIds s ON s.id = r.nameId").fetchall()
        leaf = con.execute("SELECT s.value, (e.globalTid >> 24) & 16777215 FROM SAMPLING_CALLCHAINS c "
                           "JOIN COMPOSITE_EVENTS e ON e.id = c.id JOIN StringIds s ON s.id = c.symbol "
                           "WHERE c.stackDepth = 0").fetchall()
        t0, t1 = kern[0][0], max(k[1] for k in kern)
        procs = defaultdict(lambda: {"kernels": 0, "gpu_ms": 0.0, "iv": [], "by_kernel": defaultdict(float),
                                     "by_kernel_n": defaultdict(int), "api": defaultdict(float),
                                     "api_n": defaultdict(int), "copies": defaultdict(float),
                                     "copy_ms": defaultdict(float), "leaf": defaultdict(int)})

        def role(name: str) -> str:
            n = (name or "").lower()
            return ("image pipeline" if n.startswith("component") else "detector" if "object_det" in n
                    else "ort_bench" if ("ort_bench" in n or n == "env") else n or "?")   # started via env(1)
        for s, e, gp, kname in kern:
            p = procs[role(name_of.get(gp, ""))]
            p["kernels"] += 1
            p["gpu_ms"] += (e - s) / 1e6
            p["iv"].append((s, e))
            p["by_kernel"][kname] += (e - s) / 1e6
            p["by_kernel_n"][kname] += 1
        kinds = {1: "HtoD", 2: "DtoH", 8: "DtoD", 9: "HtoH"}
        for kind, nbytes, dur, gp in mem:
            p = procs[role(name_of.get(gp, ""))]
            p["copies"][kinds.get(kind, str(kind))] += nbytes / 1e6
            p["copy_ms"][kinds.get(kind, str(kind))] += dur / 1e6
        for aname, dur, pid in api:
            p = procs[role(name_of.get(pid, ""))]
            short = re.sub(r"_v\d+$", "", aname)
            p["api"][short] += dur / 1e6
            p["api_n"][short] += 1
        total_leaf = defaultdict(int)
        for sym, pid in leaf:
            r = role(name_of.get(pid, ""))
            procs[r]["leaf"][sym] += 1
            total_leaf[r] += 1
        summary = {"span_s": (t1 - t0) / 1e9, "busy_pct": 100 * T._union_ns(
            sorted((s, e) for s, e, _, _ in kern)) / (t1 - t0), "processes": {}}
        for r, p in procs.items():
            if not p["kernels"] and not p["api"]:
                continue
            iv = sorted(p["iv"])
            win = ((iv[-1][1] - iv[0][0]) / 1e9) if iv else math.nan
            busy = T._union_ns(iv) / 1e6 if iv else 0.0
            frames = p["by_kernel_n"].get("warp", 0) if r == "image pipeline" else None
            summary["processes"][r] = {
                "kernels": p["kernels"], "gpu_ms": p["gpu_ms"], "busy_ms": busy, "window_s": win,
                "frames": frames, "frame_hz": frames / win if frames else None,
                "top_kernels": sorted(((k, v, p["by_kernel_n"][k]) for k, v in p["by_kernel"].items()),
                                      key=lambda x: -x[1])[:10],
                "api_ms": sum(p["api"].values()), "api_calls": sum(p["api_n"].values()),
                "top_api": sorted(((k, v, p["api_n"][k]) for k, v in p["api"].items()), key=lambda x: -x[1])[:10],
                "copies_mb": dict(p["copies"]), "copy_ms": dict(p["copy_ms"]),
                "leaf_total": total_leaf.get(r, 0),
                "top_leaf": sorted(p["leaf"].items(), key=lambda x: -x[1])[:8]}
        res[key] = summary
    # concurrency of the two GPU processes in the stack: time both have kernels running
    con = sqlite3.connect(f"file:{find(traces, '220718-nsys-stack-parking-trt16-kilted') / 'report.sqlite'}?mode=ro",
                          uri=True)
    lanes = {"image pipeline": [], "detector": []}
    names = {gp: n for gp, _, n in con.execute("SELECT globalPid, pid, name FROM PROCESSES")}
    for s, e, gp in con.execute("SELECT start, end, globalPid FROM CUPTI_ACTIVITY_KIND_KERNEL"):
        n = (names.get(gp) or "").lower()
        lanes["image pipeline" if n.startswith("component") else "detector"].append((s, e))
    u_ip, u_det = T._union_ns(sorted(lanes["image pipeline"])), T._union_ns(sorted(lanes["detector"]))
    u_all = T._union_ns(sorted(lanes["image pipeline"] + lanes["detector"]))
    res["overlap"] = {"busy_ip_ms": u_ip / 1e6, "busy_det_ms": u_det / 1e6, "busy_all_ms": u_all / 1e6,
                      "overlap_ms": (u_ip + u_det - u_all) / 1e6,
                      "overlap_share_of_det": (u_ip + u_det - u_all) / u_det}
    ip = res["stack"]["processes"]["image pipeline"]
    fr = ip["frames"]
    alloc = sum(v for k, v, _ in ip["top_api"] if re.match(r"cuda(Malloc|Free|MallocAsync|FreeAsync)", k))
    alloc_n = sum(n for k, _, n in ip["top_api"] if re.match(r"cuda(Malloc|Free|MallocAsync|FreeAsync)", k))
    res["per_frame"] = {
        "frames": fr, "frame_hz": ip["frame_hz"], "gpu_ms": ip["gpu_ms"] / fr, "api_ms": ip["api_ms"] / fr,
        "api_calls": ip["api_calls"] / fr, "alloc_calls": alloc_n / fr, "alloc_ms": alloc / fr,
        "htod_mb": ip["copies_mb"].get("HtoD", 0) / fr, "dtoh_mb": ip["copies_mb"].get("DtoH", 0) / fr,
        "copy_ms": sum(ip["copy_ms"].values()) / fr,
        "memcpy_leaf_share": next((n for s, n in ip["top_leaf"] if "memcpy" in s), 0) / max(ip["leaf_total"], 1)}
    det = res["stack"]["processes"]["detector"]
    res["per_inference"] = {"window_s": det["window_s"], "inferences_est": 5.0 * det["window_s"],
                            "gpu_ms": det["gpu_ms"] / (5.0 * det["window_s"]),
                            "kernels": det["kernels"] / (5.0 * det["window_s"])}
    json_ready = json.loads(json.dumps(res, default=lambda o: list(o) if isinstance(o, tuple) else str(o)))
    STATS["nsys"] = json_ready
    rows = []
    for key, s in res.items():
        if key in ("per_frame", "per_inference", "overlap"):
            continue
        for r, p in s["processes"].items():
            for k, v, n in p["top_kernels"]:
                rows.append({"trace": key, "process": r, "kind": "kernel", "name": k, "count": n, "total_ms": round(v, 3)})
            for k, v, n in p["top_api"]:
                rows.append({"trace": key, "process": r, "kind": "cuda_api", "name": k, "count": n, "total_ms": round(v, 3)})
    with open(DATA / "nsys_top.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    pf = res["per_frame"]
    lines = [r"\begin{tabular}{@{}lr@{}}", r"\toprule", r"Quantity (image pipeline, per processed frame) & Value \\", r"\midrule",
             f"processed frames in the GPU-active window & {pf['frames']} ({pf['frame_hz']:.1f} Hz) \\\\",
             f"GPU kernel time & {pf['gpu_ms']:.2f} ms \\\\",
             f"CUDA runtime API time (blocking, summed over threads) & {pf['api_ms']:.2f} ms \\\\",
             f"CUDA runtime API calls & {pf['api_calls']:.1f} \\\\",
             f"device allocations and frees (\\texttt{{cudaMalloc}}, \\texttt{{cudaFree}}, async variants) & {pf['alloc_calls']:.1f} calls, {pf['alloc_ms']:.2f} ms \\\\",
             f"host-to-device copies & {pf['htod_mb']:.2f} MB \\\\",
             f"device-to-host copies & {pf['dtoh_mb']:.2f} MB \\\\",
             f"copy engine time & {pf['copy_ms']:.2f} ms \\\\",
             f"share of CPU samples in \\texttt{{memcpy}} (leaf) & {100 * pf['memcpy_leaf_share']:.1f}\\,\\% \\\\",
             r"\bottomrule", r"\end{tabular}"]
    (TEX / "per_frame.tex").write_text("\n".join(lines) + "\n")
    return res


def fig_gpu_timeline(traces: Path) -> None:
    """One second of the stack's GPU activity: kernels and copies per process."""
    con = sqlite3.connect(f"file:{find(traces, '220718-nsys-stack-parking-trt16-kilted') / 'report.sqlite'}?mode=ro",
                          uri=True)
    names = {gp: n for gp, _, n in con.execute("SELECT globalPid, pid, name FROM PROCESSES")}
    role = lambda gp: "image pipeline" if (names.get(gp) or "").lower().startswith("component") else "detector"
    ker = con.execute("SELECT start, end, globalPid FROM CUPTI_ACTIVITY_KIND_KERNEL ORDER BY start").fetchall()
    mid = ker[len(ker) // 2][0]
    w0, w1 = mid, mid + 1_000_000_000
    fig, ax = plt.subplots(figsize=(W, 1.9))
    rows = {("image pipeline", "kernels"): 3, ("image pipeline", "copies"): 2, ("detector", "kernels"): 1,
            ("detector", "copies"): 0}
    for (r, kind), y in rows.items():
        if kind == "kernels":
            iv = [((s - w0) / 1e6, (e - s) / 1e6) for s, e, gp in ker if role(gp) == r and s < w1 and e > w0]
            ax.broken_barh(iv, (y - 0.32, 0.64), color=ROLE_COLOR[r], linewidth=0)
        else:
            for kindcode, c in ((1, SLOT[2]), (2, SLOT[3])):
                iv = [((s - w0) / 1e6, max((e - s) / 1e6, 0.15)) for s, e, gp in con.execute(
                    "SELECT start, end, globalPid FROM CUPTI_ACTIVITY_KIND_MEMCPY WHERE copyKind = ? AND start < ? "
                    "AND end > ?", (kindcode, w1, w0)) if role(gp) == r]
                ax.broken_barh(iv, (y - 0.32, 0.64), color=c, linewidth=0)
    ax.set_yticks(list(rows.values()), [f"{r}: {k}" for r, k in rows], fontsize=6.8)
    ax.set_xlim(0, 1000)
    ax.set_xlabel("time within a 1 s window of the stack trace [ms]")
    ax.grid(axis="y", visible=False)
    ax.legend(handles=[Patch(color=ROLE_COLOR["image pipeline"], label="image pipeline kernels"),
                       Patch(color=ROLE_COLOR["detector"], label="detector kernels"),
                       Patch(color=SLOT[2], label="host-to-device copy"), Patch(color=SLOT[3], label="device-to-host copy")],
              loc="lower center", ncol=4, bbox_to_anchor=(0.45, 1.0), fontsize=6.5)
    save(fig, "gpu_timeline")


def fig_nsys(res: dict) -> None:
    fig, axs = plt.subplots(2, 2, figsize=(W, 4.9), gridspec_kw={"hspace": 0.6, "wspace": 0.95})

    def bars(ax, items, title, xl):
        items = items[:8]
        y = np.arange(len(items))
        ax.barh(y, [v for _, v, _ in items], height=0.6, color=[c for _, _, c in items], edgecolor="white", linewidth=1.0)
        for i, (_, v, _) in enumerate(items):
            ax.text(v * 1.02, i, f"{v:.0f}", va="center", fontsize=6, color=INK2)
        ax.set_yticks(y, [n if len(n) <= 30 else n[:28] + "..." for n, _, _ in items], fontsize=6.2)
        ax.invert_yaxis()
        ax.set_xlim(0, max(v for _, v, _ in items) * 1.2)
        ax.set_title(title)
        ax.set_xlabel(xl)
        ax.grid(axis="y", visible=False)

    sp = res["stack"]["processes"]
    ker = sorted([(k, v, ROLE_COLOR[r]) for r in ("image pipeline", "detector") for k, v, _ in sp[r]["top_kernels"]],
                 key=lambda x: -x[1])
    api = sorted([(k, v, ROLE_COLOR[r]) for r in ("image pipeline", "detector") for k, v, _ in sp[r]["top_api"]],
                 key=lambda x: -x[1])
    bars(axs[0, 0], ker, "(a) Stack: GPU kernel time", "total over 46 s [ms]")
    bars(axs[0, 1], api, "(b) Stack: CUDA runtime API time", "total over 46 s [ms]")
    oc = res["ort_cuda"]["processes"]["ort_bench"]
    bars(axs[1, 0], [(k, v, ROLE_COLOR["detector"]) for k, v, _ in oc["top_kernels"]],
         "(c) ORT CUDA EP benchmark: GPU kernel time", "total over 5.1 s [ms]")
    bars(axs[1, 1], [(k, v, ROLE_COLOR["detector"]) for k, v, _ in oc["top_api"]],
         "(d) ORT CUDA EP benchmark: CUDA runtime API time", "total over 5.1 s [ms]")
    fig.legend(handles=[Patch(color=ROLE_COLOR["image pipeline"], label="image pipeline (component container)"),
                        Patch(color=ROLE_COLOR["detector"], label="inference (detector, ort_bench)")],
               loc="upper center", ncol=2, bbox_to_anchor=(0.5, 1.03), fontsize=6.8)
    save(fig, "nsys_breakdown")


# --------------------------------------------------------------------------------------------------- power rails
def fig_power(base: dict, det: dict, st: dict) -> None:
    rows = [("idle board", base["power_vdd_cpu_gpu_cv_mw"] / 1000, base["power_vdd_soc_mw"] / 1000,
             base["power_vdd_in_mw"] / 1000)]
    for label in ("Kilted TRT fp16", "Kilted ORT CUDA EP"):
        r = det[label][0]
        rows.append((f"detector, {label.split(' ', 1)[1]}", r["cpu_gpu_cv_w"], r["soc_w"], r["power_w"]))
    for label in ("TRT fp16, native", "ORT CUDA EP, native"):
        r = st[label]
        rows.append((f"stack, {label.split(',')[0]}", r["cpu_gpu_cv_w"], r["soc_w"], r["power_w"]))
    fig, ax = plt.subplots(figsize=(W, 1.9))
    y = np.arange(len(rows))
    parts = [("CPU/GPU/CV rail", SLOT[0]), ("SoC rail", SLOT[1]), ("rest of the module (memory, I/O, conversion)", SLOT[2])]
    left = np.zeros(len(rows))
    for i, (name, c) in enumerate(parts):
        vals = np.array([r[1] if i == 0 else r[2] if i == 1 else r[3] - r[1] - r[2] for r in rows])
        ax.barh(y, vals, left=left, height=0.6, color=c, edgecolor="white", linewidth=1.0, label=name)
        left += vals
    for i, r in enumerate(rows):
        ax.text(r[3] + 0.08, i, f"{r[3]:.2f} W", va="center", fontsize=6.3, color=INK2)
    ax.set_yticks(y, [r[0] for r in rows], fontsize=6.8)
    ax.invert_yaxis()
    ax.set_xlim(0, max(r[3] for r in rows) * 1.15)
    ax.set_xlabel("mean power [W] (VDD_IN split into its measured rails)")
    ax.grid(axis="y", visible=False)
    ax.legend(loc="upper center", ncol=3, bbox_to_anchor=(0.45, 1.32), fontsize=6.6)
    save(fig, "power_rails")
    STATS["power_rails"] = [{"config": r[0], "cpu_gpu_cv_w": r[1], "soc_w": r[2], "vdd_in_w": r[3]} for r in rows]


# ------------------------------------------------------------------------------------------------- the reset (F56)
def reset(traces: Path) -> None:
    r = Run(find(traces, "201949-launch-od-onnx-kilted-container"))
    s = r.teg
    lines = [r"\begin{tabular}{@{}rrrrrr@{}}", r"\toprule",
             r"$t$ [s] & RAM [MB] & CPU mean [\%] & GPU [\%] & VDD\_IN [mW] & $T_j$ [\si{\celsius}] \\",
             r"\midrule"]
    for i in range(len(s["t_s"])):
        lines.append(" & ".join([f(s["t_s"][i], 1), f(s["ram_used_mb"][i], 0), f(s.get("cpu_avg_pct", [math.nan] * 99)[i], 0),
                                 f(s.get("gpu_pct", [math.nan] * 99)[i], 0), f(s["power_vdd_in_mw"][i], 0),
                                 f(s["temp_tj_c"][i], 1)]) + r" \\")
    lines += [r"\bottomrule", r"\end{tabular}"]
    (TEX / "reset.tex").write_text("\n".join(lines) + "\n")
    log = (r.d / "launch.log").read_text(errors="replace").splitlines()
    STATS["reset"] = {"tegrastats_samples": int(len(s["t_s"])), "launch_log_tail": log[-6:]}


# ---------------------------------------------------------------------------------------------- comparisons
def comparisons(det: dict, stack_inf: dict) -> None:
    v = {k: x[1] for k, x in det.items()}
    pairs = [
        ("backend", "Jazzy ORT CUDA EP", "Jazzy TRT fp16", "ORT CUDA EP $\\to$ TensorRT fp16 (Jazzy)"),
        ("backend", "Jazzy TRT fp16", "Jazzy TRT INT8 (QAT)", "TensorRT fp16 $\\to$ INT8 (Jazzy)"),
        ("distro", "Jazzy TRT fp16", "Kilted TRT fp16", "Jazzy $\\to$ Kilted (TensorRT fp16)"),
        ("distro", "Jazzy ORT CUDA EP", "Kilted ORT CUDA EP", "Jazzy $\\to$ Kilted (ORT CUDA EP)"),
        ("mode", "Kilted TRT fp16", "Kilted TRT fp16, container", "native $\\to$ container (Kilted, TensorRT fp16)"),
        ("replicate", "Kilted TRT fp16", "Kilted TRT fp16 (rep.)", "run $\\to$ replicate (Kilted TensorRT fp16)"),
        ("replicate", "Jazzy ORT CUDA EP", "Jazzy ORT CUDA EP (rep.)", "run $\\to$ replicate (Jazzy ORT CUDA EP)"),
        ("load", "Kilted TRT fp16", "stack:TensorRT fp16", "alone $\\to$ in the stack (TensorRT fp16)"),
        ("load", "Kilted ORT CUDA EP", "stack:ORT CUDA EP", "alone $\\to$ in the stack (ORT CUDA EP)"),
    ]
    out, lines = [], [r"\begin{tabular}{@{}llrrrr@{}}", r"\toprule",
                      r"Factor & Comparison ($x \to y$) & $\tilde y - \tilde x$ [ms] (95\,\% CI) & $\tilde y / \tilde x$ & Cliff's $\delta$ & $n_x / n_y$ \\",
                      r"\midrule"]
    for factor, a, b, text in pairs:
        y = stack_inf[b.split(":", 1)[1]] if b.startswith("stack:") else v[b]
        if v[a] is None or y is None:   # run without per-message data (no bag CSV)
            continue
        c = compare(v[a], y)
        c.update(factor=factor, x=a, y=b)
        out.append(c)
        lines.append(" & ".join([factor, text, f"{c['diff']:+.2f} ({c['diff_ci'][0]:+.2f} to {c['diff_ci'][1]:+.2f})",
                                 f"{c['ratio']:.3f}", f"{c['cliffs_delta']:+.2f}", f"{c['n_x']}/{c['n_y']}"]) + r" \\")
    lines += [r"\bottomrule", r"\end{tabular}"]
    (TEX / "comparisons.tex").write_text("\n".join(lines) + "\n")
    STATS["comparisons"] = out


def dashboard_data(det: dict, stack_inf: dict, res: dict) -> None:
    """Small extra datasets for the measurement dashboard: per-inference samples (alone and in the stack) and the
    image pipeline's per-frame costs from the stack trace."""
    with open(DATA / "inference_samples.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["config", "inference_ms"])
        for label, (_row, v) in det.items():
            if v is not None:
                w.writerows([label, round(float(x), 3)] for x in v)
        for backend, v in stack_inf.items():
            name = "Kilted TRT fp16, in the stack" if backend == "TensorRT fp16" else "Kilted ORT CUDA EP, in the stack"
            w.writerows([name, round(float(x), 3)] for x in v)
    pf, pi, ov = res["per_frame"], res["per_inference"], res["overlap"]
    det_up = res["stack"]["processes"]["detector"]["copies_mb"].get("HtoD", 0) / pi["inferences_est"]
    rows = [("frames_per_s", pf["frame_hz"], "1/s", "frames the image pipeline processed per second"),
            ("gpu_ms_per_frame", pf["gpu_ms"], "ms", "GPU kernel time per frame"),
            ("api_ms_per_frame", pf["api_ms"], "ms", "blocking CUDA runtime API time per frame"),
            ("alloc_calls_per_frame", pf["alloc_calls"], "calls", "device allocations and frees per frame"),
            ("alloc_ms_per_frame", pf["alloc_ms"], "ms", "time in device allocations and frees per frame"),
            ("htod_mb_per_frame", pf["htod_mb"], "MB", "host-to-device copies per frame"),
            ("dtoh_mb_per_frame", pf["dtoh_mb"], "MB", "device-to-host copies per frame"),
            ("memcpy_cpu_share", pf["memcpy_leaf_share"], "share", "share of the image pipeline's CPU samples in memcpy"),
            ("gpu_busy_share", res["stack"]["busy_pct"] / 100, "share", "GPU busy time in the active window"),
            ("kernel_overlap_share", ov["overlap_share_of_det"], "share", "detector kernel time overlapping the image pipeline's"),
            ("detector_gpu_ms_per_inference", pi["gpu_ms"], "ms", "detector GPU kernel time per inference in the stack"),
            ("detector_upload_mb_per_inference", det_up, "MB", "detector host-to-device copy per inference")]
    with open(DATA / "stack_costs.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["quantity", "value", "unit", "description"])
        w.writerows([q, round(float(v), 4), u, d] for q, v, u, d in rows)


def find(traces: Path, suffix: str) -> Path:
    hits = sorted(traces.glob(f"*{suffix}"))
    if not hits:
        sys.exit(f"recording not found: {suffix}")
    return hits[0]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--traces", default=str(REPO / "traces/nano-lab"))
    traces = Path(ap.parse_args().traces)
    for d in (FIG, DATA, TEX):
        d.mkdir(parents=True, exist_ok=True)
    campaign(traces)
    base = idle(traces)
    b = bench(traces, base)
    fig_bench(b)
    det = detector(traces, base)
    st, stack_inf = stack(traces, base)
    fig_detector(det, stack_inf)
    fig_stack(st)
    memtest(traces)
    res = nsys(traces)
    fig_nsys(res)
    fig_gpu_timeline(traces)
    fig_power(base, det, st)
    reset(traces)
    comparisons(det, stack_inf)
    dashboard_data(det, stack_inf, res)
    (REPO / "docs/measurements/stats.json").write_text(json.dumps(STATS, indent=1, default=float) + "\n")
    print(json.dumps({"idle": STATS["idle"], "campaign": STATS["campaign"], "alignment": STATS["bench_alignment"],
                      "per_frame": STATS["nsys"]["per_frame"], "per_inference": STATS["nsys"]["per_inference"]},
                     indent=1, default=float))


if __name__ == "__main__":
    main()
