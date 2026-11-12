#!/usr/bin/env python3
"""Turn raw run directories into per-run metrics, summaries and statistics.

Input : experiments/results/raw/<config>/run_NN/
Output: experiments/results/
  runs.csv            one row per run (all metrics)
  summary.csv         mean, std, 95% CI per config and metric
  stats_tests.csv     Welch t-test + Mann-Whitney U, baseline vs QoS
  timeseries.csv      1 s throughput per class (long format)
  latency_samples.csv per-packet one-way delay (congested phase)
Run with the analysis venv (Python 3.11).
"""
import glob
import json
import os

import numpy as np
import pandas as pd
from scipy import stats

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, "experiments", "results")
UDP_OVERHEAD = 28  # IP + UDP header bytes added to the payload on the wire


def jitter_ms(delays):
    """RFC 3550 interarrival jitter (final smoothed value), in ms."""
    j = 0.0
    for d in np.abs(np.diff(delays)):
        j += (d - j) / 16.0
    return j * 1000


def udp_metrics(run, name, meta, phases):
    send = json.load(open(os.path.join(run, "%s_send.json" % name)))
    df = pd.read_csv(os.path.join(run, "%s_recv.csv" % name)).drop_duplicates("seq")
    df["t"] = df.send_ts - meta["t0"]
    df["delay"] = df.recv_ts - df.send_ts
    out, interval = {}, 1.0 / send["pps"]
    for ph, (a, b) in phases.items():
        planned = int(round((b - a) / interval))
        part = df[(df.t >= a) & (df.t < b)].sort_values("seq")
        if b > meta["idle"] + meta["congested"] or planned == 0:
            continue  # sender stopped before recovery
        d = part.delay.values
        out["%s_%s_lat_mean_ms" % (name, ph)] = d.mean() * 1000 if len(d) else np.nan
        out["%s_%s_lat_p95_ms" % (name, ph)] = np.percentile(d, 95) * 1000 if len(d) else np.nan
        out["%s_%s_lat_p99_ms" % (name, ph)] = np.percentile(d, 99) * 1000 if len(d) else np.nan
        out["%s_%s_jitter_ms" % (name, ph)] = jitter_ms(d) if len(d) > 1 else np.nan
        out["%s_%s_loss_pct" % (name, ph)] = max(0.0, 100.0 * (1 - len(part) / planned))
        out["%s_%s_kbps" % (name, ph)] = (part.bytes.sum() + UDP_OVERHEAD * len(part)) \
            * 8 / 1000.0 / (b - a)
    sec = (df.recv_ts - meta["t0"]).astype(int)
    ts = ((df.bytes + UDP_OVERHEAD) * 8 / 1000.0).groupby(sec).sum()
    return out, df, ts


def bulk_metrics(run, meta):
    # receiver side (iperf3 server) = delivered throughput; the client report
    # is the sender rate, which overstates what crossed the bottleneck
    path = os.path.join(run, "bulk_server.json")
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        path = os.path.join(run, "bulk_client.json")
    d = json.load(open(path))
    ints = d.get("intervals", [])
    rows = []
    for i, it in enumerate(ints):
        t = meta["idle"] + it["sum"]["start"]
        rows.append({"t": t, "kbps": it["sum"]["bits_per_second"] / 1000.0,
                     "bytes": it["sum"]["bytes"], "secs": it["sum"]["seconds"],
                     "stream_bytes": [s["bytes"] for s in it["streams"]]})
    out = {}
    cong_end = meta["idle"] + meta["congested"]
    for ph, cond in (("congested", lambda r: r["t"] < cong_end),
                     ("recovery", lambda r: r["t"] >= cong_end)):
        sel = [r for r in rows if cond(r)]
        # total bytes / total time: robust to the very short intervals iperf3
        # reports when a delayed burst arrives (averaging per-interval rates is not)
        secs = sum(r["secs"] for r in sel)
        out["bulk_%s_kbps" % ph] = (sum(r["bytes"] for r in sel) * 8 / 1000.0 / secs
                                    if sel and secs > 0 else np.nan)
        if sel:  # Jain's fairness index across bulk streams (bytes per stream)
            per = np.sum([r["stream_bytes"] for r in sel], axis=0).astype(float)
            out["bulk_%s_jain" % ph] = per.sum() ** 2 / (len(per) * (per ** 2).sum())
    ts = pd.Series({int(r["t"]): r["kbps"] for r in rows})
    return out, ts


def controller_metrics(run, meta):
    out = {}
    series = os.path.join(run, "monitor_series.jsonl")
    if os.path.exists(series):
        recs = [json.loads(l) for l in open(series) if l.strip()]
        a, b = meta["t0"] + meta["idle"], meta["t0"] + meta["idle"] + meta["congested"]
        utils = [r["util"] for r in recs if a <= r["ts"] < b]
        out["link_util_congested"] = np.mean(utils) if utils else np.nan
        out["packet_in_total"] = recs[-1]["packet_in"] if recs else np.nan
    log = os.path.join(run, "classification_log.csv")
    if os.path.exists(log) and os.path.getsize(log) > 0:
        cl = pd.read_csv(log)
        setup = cl.reason.str.extract(r"setup_us=(\d+)")[0].dropna().astype(float)
        out["flow_setups"] = len(setup)
        out["flow_setup_us_mean"] = setup.mean() if len(setup) else np.nan
    ev = os.path.join(run, "adaptation_events.csv")
    if os.path.exists(ev) and os.path.getsize(ev) > 0:
        out["adaptation_events"] = len(pd.read_csv(ev).query("dpid == 1"))
    return out


def parse_run(run):
    meta = json.load(open(os.path.join(run, "meta.json")))
    i, c, r = meta["idle"], meta["congested"], meta["recovery"]
    phases = {"idle": (0, i), "congested": (i, i + c)}
    row = {"config": os.path.basename(os.path.dirname(run)), "mode": meta["mode"],
           "bw_mbps": meta["bw_mbps"], "bulk_flows": meta["bulk_flows"], "run": meta["run"]}
    ts_rows, samples = [], []
    for name in ("voip", "video"):
        m, df, ts = udp_metrics(run, name, meta, phases)
        row.update(m)
        ts_rows += [{"t": int(t), "cls": name, "kbps": v} for t, v in ts.items()]
        cong = df[(df.t >= i) & (df.t < i + c)]
        samples += [{"cls": name, "delay_ms": d * 1000} for d in cong.delay.values]
    m, ts = bulk_metrics(run, meta)
    row.update(m)
    ts_rows += [{"t": int(t), "cls": "bulk", "kbps": v} for t, v in ts.items()]
    row.update(controller_metrics(run, meta))
    for x in ts_rows + samples:
        x.update(config=row["config"], mode=row["mode"], run=row["run"])
    return row, ts_rows, samples


def ci95(x):
    x = np.asarray(x, dtype=float)
    x = x[~np.isnan(x)]
    if len(x) < 2:
        return np.nan
    return stats.t.ppf(0.975, len(x) - 1) * x.std(ddof=1) / np.sqrt(len(x))


def main():
    runs = sorted(glob.glob(os.path.join(RES, "raw", "*", "run_*", "meta.json")))
    rows, ts_all, samp_all = [], [], []
    for meta in runs:
        try:
            r, ts, s = parse_run(os.path.dirname(meta))
        except Exception as exc:  # report and keep going; never invent numbers
            print("WARN cannot parse %s: %s" % (os.path.dirname(meta), exc))
            continue
        rows.append(r)
        ts_all += ts
        samp_all += s
    df = pd.DataFrame(rows).sort_values(["config", "run"])
    df.to_csv(os.path.join(RES, "runs.csv"), index=False)
    pd.DataFrame(ts_all).to_csv(os.path.join(RES, "timeseries.csv"), index=False)
    pd.DataFrame(samp_all).to_csv(os.path.join(RES, "latency_samples.csv"), index=False)
    metrics = [c for c in df.columns if c not in ("config", "mode", "bw_mbps", "bulk_flows", "run")]
    summ = []
    for cfg, g in df.groupby("config"):
        for m in metrics:
            summ.append({"config": cfg, "metric": m, "n": int(g[m].notna().sum()),
                         "mean": g[m].mean(), "std": g[m].std(ddof=1), "ci95": ci95(g[m])})
    pd.DataFrame(summ).to_csv(os.path.join(RES, "summary.csv"), index=False)
    tests = []
    base = df[df.config == "baseline_bw10_f2"]
    for qos in ("static_bw10_f2", "adaptive_bw10_f2"):
        q = df[df.config == qos]
        for m in ("voip_congested_lat_mean_ms", "voip_congested_lat_p95_ms",
                  "voip_congested_loss_pct", "voip_congested_jitter_ms",
                  "video_congested_lat_mean_ms", "video_congested_loss_pct"):
            if m not in df or len(base) < 2 or len(q) < 2:
                continue
            a, b = base[m].dropna(), q[m].dropna()
            t = stats.ttest_ind(a, b, equal_var=False)
            u = stats.mannwhitneyu(a, b, alternative="two-sided")
            tests.append({"baseline": "baseline_bw10_f2", "qos": qos, "metric": m,
                          "baseline_mean": a.mean(), "qos_mean": b.mean(),
                          "welch_t": t.statistic, "welch_p": t.pvalue,
                          "mannwhitney_u": u.statistic, "mannwhitney_p": u.pvalue})
    pd.DataFrame(tests).to_csv(os.path.join(RES, "stats_tests.csv"), index=False)
    print("parsed %d runs -> %s" % (len(df), RES))


if __name__ == "__main__":
    main()
