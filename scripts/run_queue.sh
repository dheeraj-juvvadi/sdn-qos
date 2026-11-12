#!/usr/bin/env bash
# Reduced-repetition reproduction queue (5 main runs, 2 per sweep point).
cd "$(dirname "$0")/.."
for d in experiments/results/raw/*/run_*; do [ -f "$d/meta.json" ] || rm -rf "$d"; done
python3 experiments/run_experiments.py --runs 5 --sweep --sweep-runs 2 --meter-only
python3 experiments/challenges.py all
python3 experiments/rest_demo.py
echo ALLDONE
