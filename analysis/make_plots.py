#!/usr/bin/env python3
"""Figures for the report, from the CSVs written by parse_results.py.

Writes PNGs to docs/figures/ and experiments/results/figures/.
"""
import json
import os
import shutil

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, "experiments", "results")
FIG = os.path.join(ROOT, "docs", "figures")
MAIN = ["baseline_bw10_f2", "static_bw10_f2", "adaptive_bw10_f2"]
LABEL = {"baseline_bw10_f2": "Baseline (no QoS)", "static_bw10_f2": "SDN-QoS static",
         "adaptive_bw10_f2": "SDN-QoS adaptive"}
COLOR = {"baseline_bw10_f2": "#c0392b", "static_bw10_f2": "#2471a3",
         "adaptive_bw10_f2": "#1e8449", "voip": "#8e44ad", "video": "#d68910",
         "bulk": "#566573"}
plt.rcParams.update({"font.size": 11, "axes.grid": True, "grid.alpha": 0.3,
                     "figure.dpi": 130, "savefig.bbox": "tight"})


def save(fig, name):
    os.makedirs(FIG, exist_ok=True)
    os.makedirs(os.path.join(RES, "figures"), exist_ok=True)
    path = os.path.join(FIG, name)
    fig.savefig(path)
    shutil.copy(path, os.path.join(RES, "figures", name))
    plt.close(fig)
    print("wrote", path)


def latency_cdf(samples):
    fig, axes = plt.subplots(1, 2, figsize=(11, 4))
    for ax, cls in zip(axes, ("voip", "video")):
        for cfg in MAIN:
            d = np.sort(samples[(samples.config == cfg) & (samples.cls == cls)].delay_ms.values)
            if len(d):
                ax.plot(d, np.arange(1, len(d) + 1) / len(d), label=LABEL[cfg], color=COLOR[cfg])
        ax.set_xscale("log")
        ax.set_xlabel("One-way delay (ms, log scale)")
        ax.set_ylabel("CDF")
        ax.set_title("%s delay under congestion (all runs)" % cls.capitalize())
        ax.legend(fontsize=9)
    save(fig, "latency_cdf.png")


def throughput_timeline(ts, runs):
    fig, axes = plt.subplots(1, 3, figsize=(15, 4), sharey=True)
    run_id = int(runs.run.min())
    for ax, cfg in zip(axes, MAIN):
        meta = json.load(open(os.path.join(RES, "raw", cfg, "run_%02d" % run_id, "meta.json")))
        part = ts[(ts.config == cfg) & (ts.run == run_id)]
        for cls in ("voip", "video", "bulk"):
            s = part[part.cls == cls].sort_values("t")
            ax.plot(s.t, s.kbps / 1000.0, label=cls, color=COLOR[cls], lw=1.6)
        a, b = meta["idle"], meta["idle"] + meta["congested"]
        ax.axvspan(a, b, color="#f5b7b1", alpha=0.25, label="congested phase")
        ax.set_title("%s (run %d)" % (LABEL[cfg], run_id))
        ax.set_xlabel("Time (s)")
        ax.set_xlim(0, b + meta["recovery"])
    axes[0].set_ylabel("Received throughput (Mbit/s)")
    axes[0].legend(fontsize=9, loc="upper left")
    save(fig, "throughput_timeline.png")


def bars(runs):
    metrics = [("voip_congested_lat_mean_ms", "VoIP mean delay (ms)"),
               ("voip_congested_jitter_ms", "VoIP jitter (ms)"),
               ("voip_congested_loss_pct", "VoIP loss (%)"),
               ("video_congested_loss_pct", "Video loss (%)"),
               ("bulk_congested_kbps", "Bulk throughput, congested (kbit/s)"),
               ("bulk_recovery_kbps", "Bulk throughput, recovery (kbit/s)")]
    fig, axes = plt.subplots(2, 3, figsize=(14, 7))
    for ax, (m, title) in zip(axes.flat, metrics):
        g = runs[runs.config.isin(MAIN)].groupby("config")[m]
        mean, std = g.mean().reindex(MAIN), g.std().reindex(MAIN)
        ax.bar(range(3), mean.values, yerr=std.values, capsize=5,
               color=[COLOR[c] for c in MAIN])
        ax.set_xticks(range(3))
        ax.set_xticklabels(["Baseline", "Static", "Adaptive"])
        ax.set_title(title, fontsize=10)
    fig.suptitle("Congested phase, mean ± std over runs (bottleneck 10 Mbit/s, 2 bulk streams)")
    fig.tight_layout()
    save(fig, "metrics_bars.png")


def adaptation_timeline(runs, cfg="adaptive_bw10_f2", name="adaptation_timeline.png"):
    if not (runs.config == cfg).any():
        return
    run_id = int(runs[runs.config == cfg].run.min())
    d = os.path.join(RES, "raw", cfg, "run_%02d" % run_id)
    meta = json.load(open(os.path.join(d, "meta.json")))
    recs = [json.loads(l) for l in open(os.path.join(d, "monitor_series.jsonl")) if l.strip()]
    t = np.array([r["ts"] - meta["t0"] for r in recs])
    hp = np.array([r["delivered"].get("voip", 0) + r["delivered"].get("video", 0)
                   for r in recs]) / 1000.0
    bulk = np.array([r["delivered"].get("bulk", 0) for r in recs]) / 1000.0
    meter = np.array([r["bulk_meter_kbps"] or np.nan for r in recs], dtype=float) / 1000.0
    fig, ax = plt.subplots(figsize=(11, 4.5))
    ax.plot(t, hp, color=COLOR["video"], label="VoIP+video delivered (s2)")
    ax.plot(t, bulk, color=COLOR["bulk"], label="Bulk delivered (s2, pre-meter count)")
    ax.step(t, meter, where="post", color="#1e8449", lw=2.2, label="Bulk meter rate")
    ev = pd.read_csv(os.path.join(d, "adaptation_events.csv"))
    ev = ev[ev.dpid == 1]
    for _, e in ev.iterrows():
        x = e.ts - meta["t0"]
        ax.axvline(x, color="k", ls=":", lw=1)
        ax.annotate(e.reason.split(":")[0], (x, 10.4),
                    rotation=90, fontsize=8, ha="right", va="top")
    a, b = meta["idle"], meta["idle"] + meta["congested"]
    ax.axvspan(a, b, color="#f5b7b1", alpha=0.25, label="congested phase")
    ax.set_xlim(-2, b + meta["recovery"] + 2)
    ax.set_ylim(0, 11)
    ax.set_xlabel("Time since traffic start (s)")
    ax.set_ylabel("Rate (Mbit/s)")
    ax.set_title("Adaptive loop, %s: bulk meter set by the monitor (run %d)" % (cfg, run_id))
    ax.legend(fontsize=8, loc="center left")
    save(fig, name)


def sweep(runs):
    if not runs.config.str.contains("_bw5_|_bw20_|_f1|_f4").any():
        return
    fig, axes = plt.subplots(1, 3, figsize=(15, 4))
    for mode, col in (("baseline", "#c0392b"), ("adaptive", "#1e8449")):
        r = runs[(runs["mode"] == mode) & (runs.bulk_flows == 2)]
        g = r.groupby("bw_mbps")["voip_congested_lat_p95_ms"]
        axes[0].errorbar(g.mean().index, g.mean().values, yerr=g.std().values, marker="o",
                         capsize=4, color=col, label=mode)
        r = runs[(runs["mode"] == mode) & (runs.bw_mbps == 10)]
        g = r.groupby("bulk_flows")["voip_congested_loss_pct"]
        axes[1].errorbar(g.mean().index, g.mean().values, yerr=g.std().values, marker="o",
                         capsize=4, color=col, label=mode)
        g = r.groupby("bulk_flows")["bulk_congested_jain"]
        axes[2].errorbar(g.mean().index, g.mean().values, yerr=g.std().values, marker="o",
                         capsize=4, color=col, label=mode)
    axes[0].set(xlabel="Bottleneck bandwidth (Mbit/s)", ylabel="VoIP p95 delay (ms)",
                yscale="log", title="VoIP p95 delay vs bottleneck (2 bulk streams)")
    axes[1].set(xlabel="Bulk TCP streams", ylabel="VoIP loss (%)",
                title="VoIP loss vs bulk load (10 Mbit/s)")
    axes[2].set(xlabel="Bulk TCP streams", ylabel="Jain's fairness index",
                title="Fairness among bulk streams", ylim=(0, 1.05))
    for ax in axes:
        ax.legend(fontsize=9)
    save(fig, "sweep.png")


def main():
    runs = pd.read_csv(os.path.join(RES, "runs.csv"))
    samples = pd.read_csv(os.path.join(RES, "latency_samples.csv"))
    ts = pd.read_csv(os.path.join(RES, "timeseries.csv"))
    latency_cdf(samples)
    throughput_timeline(ts, runs[runs.config.isin(MAIN)])
    bars(runs)
    adaptation_timeline(runs)
    adaptation_timeline(runs, "adaptive_bw10_f2_meteronly", "adaptation_timeline_meteronly.png")
    sweep(runs)


if __name__ == "__main__":
    main()
