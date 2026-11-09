#!/usr/bin/env python3
"""Plots and Markdown reports for jxb recordings. Runs in the dev container (matplotlib), via `jxb trace plot` and
`jxb trace compare`, after `jxb trace report` has written summary.json.

  jxb_plot.py DIR [DIR ...]            per recording: DIR/plots/*.png and DIR/report.md
  jxb_plot.py --compare OUT DIR ...    several recordings side by side: OUT/compare.md and OUT/*.png
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jxb_trace  # noqa: E402

plt.rcParams.update({"figure.dpi": 110, "axes.grid": True, "grid.alpha": 0.3, "axes.spines.top": False,
                     "axes.spines.right": False, "font.size": 9})


def _col(rows: list[dict], key: str, scale: float = 1.0) -> list[float]:
    return [float(r[key]) * scale if r.get(key) not in (None, "") else math.nan for r in rows]


def plot_tegrastats(d: Path, out: Path) -> Path | None:
    f = d / "tegrastats.csv"
    if not f.exists():
        return None
    rows = list(csv.DictReader(open(f)))
    if not rows:
        return None
    t = _col(rows, "t_s")
    keys = rows[0].keys()
    fig, ax = plt.subplots(5, 1, figsize=(10, 11), sharex=True)
    ax[0].plot(t, _col(rows, "cpu_avg_pct"), label="CPU mean of cores")
    ax[0].plot(t, _col(rows, "cpu_max_pct"), label="busiest core", alpha=0.6)
    ax[0].set_ylabel("CPU %")
    if "gpu_pct" in keys:
        ax[1].plot(t, _col(rows, "gpu_pct"), color="tab:green", label="GPU (GR3D)")
    if "emc_pct" in keys:
        ax[1].plot(t, _col(rows, "emc_pct"), color="tab:olive", label="memory controller (EMC)", alpha=0.7)
    ax[1].set_ylabel("load %")
    ax[2].plot(t, _col(rows, "ram_used_mb", 1 / 1024), color="tab:purple", label="RAM used")
    ax[2].set_ylabel("GB")
    for k in sorted(k for k in keys if k.startswith("power_")):
        ax[3].plot(t, _col(rows, k, 1e-3), label=k[6:-3].upper())
    ax[3].set_ylabel("W")
    for k in sorted(k for k in keys if k.startswith("temp_")):
        ax[4].plot(t, _col(rows, k), label=k[5:-2], alpha=0.8)
    ax[4].set_ylabel("°C")
    ax[4].set_xlabel("time [s]")
    for a in ax:
        a.legend(loc="upper right", fontsize=7, ncol=3)
    ax[0].set_title(f"{d.name}: board telemetry (tegrastats)")
    fig.tight_layout()
    p = out / "tegrastats.png"
    fig.savefig(p)
    plt.close(fig)
    return p


def plot_processes(d: Path, s: dict, out: Path) -> Path | None:
    pr = s.get("processes") or {}
    procs = [r for r in pr.get("session", []) if r.get("series")][:8]
    if not procs:
        return None
    fig, ax = plt.subplots(2, 1, figsize=(10, 7), sharex=True)
    for r in procs:
        t = [x[0] for x in r["series"]]
        label = f"{r['comm']} ({r['pid']})"
        ax[0].plot(t, [x[1] for x in r["series"]], label=label)
        ax[1].plot(t, [x[2] for x in r["series"]], label=label)
    ax[0].set_ylabel("CPU % (100 = 1 core)")
    ax[1].set_ylabel("RSS MB")
    ax[1].set_xlabel("time [s]")
    ax[0].legend(loc="upper right", fontsize=7, ncol=2)
    ax[0].set_title(f"{d.name}: processes of the session")
    fig.tight_layout()
    p = out / "processes.png"
    fig.savefig(p)
    plt.close(fig)
    return p


def plot_bench(d: Path, s: dict, out: Path) -> Path | None:
    be = s.get("bench")
    if not be:
        return None
    models = [(m, [r for r in rows if r.get("p50_ms")]) for m, rows in be["models"].items()]
    models = [(m, rows) for m, rows in models if rows]
    if not models:
        return None
    n = sum(len(rows) for _, rows in models)
    fig, ax = plt.subplots(figsize=(10, 1.0 + 0.32 * n + 0.4 * len(models)))
    labels, vals, p99, colors = [], [], [], []
    palette = plt.get_cmap("tab10")
    for i, (m, rows) in enumerate(models):
        for r in sorted(rows, key=lambda r: -r["p50_ms"]):
            labels.append(f"{m} | {r['backend']}")
            vals.append(r["p50_ms"])
            p99.append(r.get("p99_ms") or r["p50_ms"])
            colors.append(palette(i % 10))
    y = range(len(labels))
    ax.barh(y, vals, color=colors)
    ax.scatter(p99, y, marker="|", color="black", s=80, label="p99", zorder=3)
    for yi, v in zip(y, vals):
        ax.text(v, yi, f"  {v:.1f} ms ({1000 / v:.0f} FPS)", va="center", fontsize=7)
    ax.set_yticks(list(y), labels, fontsize=7)
    ax.set_xscale("log")
    ax.set_xlabel("latency per inference, p50 [ms] (log)")
    ax.set_title(f"{d.name}: inference benchmark")
    ax.legend(loc="lower right")
    fig.tight_layout()
    p = out / "bench.png"
    fig.savefig(p)
    plt.close(fig)
    return p


def plot_nsys(d: Path, s: dict, out: Path) -> Path | None:
    ks = ((s.get("nsys") or {}).get("gpu") or {}).get("top_kernels") or []
    if not ks:
        return None
    ks = ks[:12][::-1]
    fig, ax = plt.subplots(figsize=(10, 0.8 + 0.35 * len(ks)))
    ax.barh(range(len(ks)), [k["total_ms"] for k in ks], color="tab:green")
    ax.set_yticks(range(len(ks)), [k["name"][:70] for k in ks], fontsize=7)
    ax.set_xlabel("total GPU time [ms]")
    ax.set_title(f"{d.name}: top CUDA kernels (Nsight Systems)")
    fig.tight_layout()
    p = out / "nsys_kernels.png"
    fig.savefig(p)
    plt.close(fig)
    return p


def plot_kernel(d: Path, s: dict, out: Path) -> Path | None:
    k = s.get("kernel")
    if not k or not k.get("top_tasks"):
        return None
    tasks = k["top_tasks"][:12][::-1]
    fig, ax = plt.subplots(1, 2, figsize=(11, 0.8 + 0.35 * len(tasks)), sharey=True)
    names = [f"{t['comm']} ({t['pid']})" for t in tasks]
    ax[0].barh(range(len(tasks)), [t["cpu_pct"] for t in tasks], color="tab:blue")
    ax[0].set_yticks(range(len(tasks)), names, fontsize=7)
    ax[0].set_xlabel("CPU % of the recording")
    ax[1].barh(range(len(tasks)), [(t["wakeup_us"] or {}).get("p99", 0) for t in tasks], color="tab:red")
    ax[1].set_xlabel("wakeup latency p99 [µs]")
    fig.suptitle(f"{d.name}: scheduler (ftrace)")
    fig.tight_layout()
    p = out / "kernel.png"
    fig.savefig(p)
    plt.close(fig)
    return p


def plot_bag(d: Path, s: dict, out: Path) -> Path | None:
    files = sorted((d / "bag/csv").glob("*.csv"))
    series = []
    for f in files:
        rows = list(csv.DictReader(open(f)))
        for col in ("value", "latency_ms"):
            vals = [float(r[col]) for r in rows if r[col] not in ("", "None")]
            if len(vals) > 1:
                t0 = int(rows[0]["t_ns"])
                ts = [(int(r["t_ns"]) - t0) / 1e9 for r in rows if r[col] not in ("", "None")]
                series.append((f"/{f.stem.replace('__', '/')} {col}", ts, vals))
    if not series:
        return None
    fig, ax = plt.subplots(len(series), 2, figsize=(11, 2.6 * len(series)), squeeze=False)
    for i, (name, ts, vals) in enumerate(series):
        ax[i][0].plot(ts, vals, lw=0.8)
        ax[i][0].set_title(name, fontsize=8)
        ax[i][0].set_xlabel("time [s]")
        ax[i][1].hist(vals, bins=40, color="tab:orange")
        ax[i][1].set_title(f"distribution (n={len(vals)})", fontsize=8)
    fig.tight_layout()
    p = out / "bag.png"
    fig.savefig(p)
    plt.close(fig)
    return p


def plot_memory(d: Path, out: Path) -> Path | None:
    """The system's memory counters of `jxb launch --monitor` (meminfo.csv) over time."""
    f = d / "meminfo.csv"
    if not f.exists():
        return None
    rows = list(csv.DictReader(open(f)))
    if len(rows) < 2:
        return None
    t = [float(r["t_s"]) / 60 for r in rows]
    fig, ax = plt.subplots(figsize=(11, 3.2))
    for key in ("MemAvailable", "MemFree", "Cached", "Shmem", "Slab"):
        if key in rows[0]:
            ax.plot(t, _col(rows, key, 1 / 1024), lw=1.0, label=key)
    ax.set_xlabel("time [min]")
    ax.set_ylabel("GB")
    ax.set_title(f"{d.name}: system memory (/proc/meminfo)", fontsize=9)
    ax.legend(fontsize=7, ncol=5, loc="upper right")
    fig.tight_layout()
    p = out / "memory.png"
    fig.savefig(p)
    plt.close(fig)
    return p


def plot_rates(d: Path, out: Path) -> Path | None:
    """Topic rates of `jxb launch --rates` (rates.log) over time."""
    f = d / "rates.log"
    if not f.exists():
        return None
    series: dict[str, list[tuple[float, float]]] = {}
    for line in f.read_text(errors="replace").splitlines():
        if line.startswith('{"probe"'):
            r = json.loads(line)
            series.setdefault(r["probe"], []).append((r["t"], r["hz"]))
    if not series:
        return None
    t0 = min(v[0][0] for v in series.values())
    fig, ax = plt.subplots(figsize=(11, 3.0))
    for topic, pts in series.items():
        ax.plot([p[0] - t0 for p in pts], [p[1] for p in pts], lw=1.0, label=topic)
    ax.set_xlabel("time [s]")
    ax.set_ylabel("Hz")
    ax.set_ylim(bottom=0)
    ax.set_title(f"{d.name}: topic rates (jxb_rate_probe)", fontsize=9)
    ax.legend(fontsize=7)
    fig.tight_layout()
    p = out / "rates.png"
    fig.savefig(p)
    plt.close(fig)
    return p


def report(d: Path) -> Path:
    s = json.loads((d / "summary.json").read_text())
    out = d / "plots"
    out.mkdir(exist_ok=True)
    plots = [p for p in (plot_tegrastats(d, out), plot_processes(d, s, out), plot_memory(d, out),
                         plot_rates(d, out), plot_bench(d, s, out), plot_nsys(d, s, out), plot_kernel(d, s, out),
                         plot_bag(d, s, out)) if p]
    md = [f"# {d.name}", "", "```text", jxb_trace.format_summary(s), "```", ""]
    md += [f"![{p.stem}](plots/{p.name})" for p in plots]
    (d / "report.md").write_text("\n".join(md) + "\n")
    print(f"{d}/report.md ({len(plots)} plots)")
    return d / "report.md"


def _metrics(s: dict) -> dict:
    m = s.get("meta", {})
    t = (s.get("tegrastats") or {}).get("metrics", {})
    pr = s.get("processes") or {}
    bag = (s.get("bag") or {}).get("series", {})
    inf = next((v for k, v in bag.items() if k.endswith("inference_time:value")), {})
    la = s.get("launch") or {}
    bs = s.get("board_state")

    def mt(key, field="mean", scale=1.0):
        return round(t[key][field] * scale, 2) if key in t else None
    return {"recording": s["trace"], "kind": m.get("kind"), "profile": m.get("profile"), "mode": m.get("mode"),
            "label": m.get("label"), "wall_s": m.get("wall_s"),
            "session_cpu_pct": pr.get("session_cpu_pct"), "session_rss_mb": pr.get("session_rss_mb"),
            "cpu_mean_pct": mt("cpu_avg_pct"), "gpu_mean_pct": mt("gpu_pct"), "gpu_max_pct": mt("gpu_pct", "max"),
            "ram_max_gb": mt("ram_used_mb", "max", 1 / 1024), "vdd_in_mean_w": mt("power_vdd_in_mw", "mean", 1e-3),
            "energy_j": (s.get("tegrastats") or {}).get("energy_j"), "inference_ms_mean": inf.get("mean"),
            "inference_ms_p99": inf.get("p99"), "inferences": inf.get("n"),
            "crashed": len(la.get("crashed", [])) if la else None,
            "fixed_clocks": (", ".join(bs["fixed"]) or "none") if bs else None}


def board_differences(sums: list[dict], names: list[str]) -> list[str]:
    """Markdown: the board settings (power mode, governors, clock limits, ...) that differ between the recordings."""
    sets = []
    for s in sums:
        bs = s.get("board_state") or {}
        st = bs.get("start") or bs.get("end")
        sets.append(jxb_trace.board_settings(st) if st else None)
    known = [x for x in sets if x is not None]
    if not known:
        return []
    differ = [k for k in sorted({k for x in known for k in x}) if len({x.get(k) for x in known}) > 1]
    md = ["", "## Board settings"]
    if differ:
        md += ["The board's settings differ between the recordings, so their results are not directly comparable:",
               "", "| setting | " + " | ".join(names) + " |", "|" + "---|" * (len(names) + 1)]
        md += ["| " + k + " | " + " | ".join("not recorded" if x is None else x.get(k, "-") for x in sets) + " |"
               for k in differ]
        print("board settings differ: " + ", ".join(differ))
    else:
        md.append(f"The same in the {len(known)} recordings that recorded them (power mode, governors, clock limits, "
                  "CPU idle states, rail gating, fan control).")
    missing = [n for n, x in zip(names, sets) if x is None]
    if missing:
        md += ["", f"Not recorded (recordings before jxb recorded the board state): {', '.join(missing)}"]
    return md


def compare(out: Path, dirs: list[Path]) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    sums = [json.loads((d / "summary.json").read_text()) for d in dirs]
    rows = [_metrics(s) for s in sums]
    cols = [c for c in rows[0] if any(r.get(c) is not None for r in rows)]
    md = [f"# Comparison of {len(rows)} recordings", "", "| " + " | ".join(cols) + " |",
          "|" + "---|" * len(cols)]
    md += ["| " + " | ".join("" if r.get(c) is None else str(r[c]) for c in cols) + " |" for r in rows]
    names = [r["label"] or r["recording"][-28:] for r in rows]
    panels = [(k, t) for k, t in (("session_cpu_pct", "CPU of the session [% of a core]"),
                                  ("session_rss_mb", "RSS of the session [MB]"),
                                  ("gpu_mean_pct", "GPU load, mean [%]"), ("vdd_in_mean_w", "board power, mean [W]"),
                                  ("inference_ms_mean", "inference time, mean [ms]"),
                                  ("inference_ms_p99", "inference time, p99 [ms]"))
              if any(r.get(k) is not None for r in rows)]
    if panels:
        fig, ax = plt.subplots(len(panels), 1, figsize=(10, 2.2 * len(panels) + 0.5), squeeze=False)
        for a, (k, title) in zip(ax[:, 0], panels):
            vals = [r.get(k) or 0 for r in rows]
            a.bar(range(len(rows)), vals, color=plt.get_cmap("tab10")(range(len(rows))))
            a.set_title(title, fontsize=9)
            a.set_xticks(range(len(rows)), names, fontsize=7, rotation=15)
            for i, v in enumerate(vals):
                a.text(i, v, f"{v:g}", ha="center", va="bottom", fontsize=7)
        fig.tight_layout()
        fig.savefig(out / "compare.png")
        plt.close(fig)
        md += ["", "![comparison](compare.png)"]
    md += board_differences(sums, names)
    (out / "compare.md").write_text("\n".join(md) + "\n")
    (out / "compare.json").write_text(json.dumps(rows, indent=2) + "\n")
    print(f"{out}/compare.md")
    return out / "compare.md"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dirs", nargs="+", type=Path)
    ap.add_argument("--compare", type=Path, metavar="OUT")
    a = ap.parse_args()
    if a.compare:
        compare(a.compare, a.dirs)
    else:
        for d in a.dirs:
            report(d)


if __name__ == "__main__":
    main()
