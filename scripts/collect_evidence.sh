#!/usr/bin/env bash
# Copy rule-installation evidence (flows, meters, queues, logs) into docs/evidence/.
set -eu
cd "$(dirname "$0")/.."
R=experiments/results/raw; E=docs/evidence; mkdir -p $E
for m in baseline static adaptive; do
  d=$R/${m}_bw10_f2/run_01
  [ -d "$d" ] || continue
  cp "$d/dump_congested.txt" "$E/dump_${m}_congested.txt"
  grep -E "STAGE|adapt|start" "$d/controller.log" | head -60 > "$E/controller_${m}_stages.log" || true
  head -40 "$d/classification_log.csv" > "$E/classification_${m}.csv" || true
done
cp $R/adaptive_bw10_f2/run_01/adaptation_events.csv $E/adaptation_events_run01.csv 2>/dev/null || true
echo "evidence in $E:"; ls $E
