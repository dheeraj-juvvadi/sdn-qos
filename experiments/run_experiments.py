#!/usr/bin/env python3
"""Repeat the scenario N times per configuration (baseline vs SDN-QoS).

Main comparison (bw 10 Mbit/s, 2 bulk streams):
  baseline | static | adaptive        x --runs (default 10)
Optional sweep (--sweep): bottleneck 5/10/20 Mbit/s and bulk 1/2/4 streams for
baseline vs adaptive, --sweep-runs each (default 3).

Raw output: experiments/results/raw/<config>/run_NN/. Completed runs (meta.json
present) are skipped, so an interrupted batch can be resumed. Afterwards run
analysis/parse_results.py (make plots does both).

Usage: sudo python3 experiments/run_experiments.py [--runs 10] [--modes baseline,static]
"""
import argparse
import fcntl
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "experiments", "results", "raw")


def config_name(mode, bw, flows, tag=""):
    return "%s_bw%d_f%d%s" % (mode, bw, flows, tag)


def run_one(mode, bw, flows, run, policy=None, tag=""):
    out = os.path.join(RAW, config_name(mode, bw, flows, tag), "run_%02d" % run)
    if os.path.exists(os.path.join(out, "meta.json")):
        print("skip (done) %s" % out)
        return
    os.makedirs(out, exist_ok=True)
    cmd = [sys.executable, os.path.join(ROOT, "traffic", "run_scenario.py"), "--mode", mode,
           "--bw", str(bw), "--bulk-flows", str(flows), "--run", str(run), "--out", out]
    if policy:
        cmd += ["--policy", policy]
    t = time.time()
    with open(os.path.join(out, "scenario_stdout.log"), "w") as fh:
        rc = subprocess.call(cmd, stdout=fh, stderr=subprocess.STDOUT)
    print("%-28s run %02d rc=%d %.0fs" % (config_name(mode, bw, flows, tag), run, rc, time.time() - t),
          flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--modes", default="baseline,static,adaptive")
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--sweep-runs", type=int, default=3)
    ap.add_argument("--meter-only", action="store_true",
                    help="R1 demo: meter-only policy with a loose bulk cap, static vs adaptive")
    a = ap.parse_args()
    if os.geteuid() != 0:
        sys.exit("run as root (Mininet needs it): sudo make run-all")
    lock = open("/tmp/sdn-qos-experiments.lock", "w")
    try:  # Mininet runs are global; two batches would destroy each other
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        sys.exit("another experiment batch is already running")
    modes = a.modes.split(",")
    for run in range(1, a.runs + 1):          # interleave modes to spread drift
        for mode in modes:
            run_one(mode, 10, 2, run)
    if a.sweep:
        for mode in ("baseline", "adaptive"):
            for bw, flows in ((5, 2), (20, 2), (10, 1), (10, 4)):
                for run in range(1, a.sweep_runs + 1):
                    run_one(mode, bw, flows, run)
    if a.meter_only:
        pol = os.path.join(ROOT, "policies", "meter_only_policy.yaml")
        for run in range(1, a.sweep_runs + 1):
            for mode in ("static", "adaptive"):
                run_one(mode, 10, 2, run, policy=pol, tag="_meteronly")


if __name__ == "__main__":
    main()
