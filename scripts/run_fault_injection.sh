#!/bin/zsh
# Fault injection v1: configs under F1-F3, one round, strictly one run at a time.
# Resumable per item (see scripts/lib_run.zsh); stops at once when the model quota is exhausted.
set -u
cd "$(dirname "$0")/.."
source scripts/lib_run.zsh
ROUND=${ROUND:-1}
CONFIGS=(${=CONFIGS:-b2_flat_repair b3_graph_repair})
for c in $CONFIGS; do cost_gate $c 132; canary_gate $c; done
for f in F1 F2 F3; do for c in $CONFIGS; do
  bench_run data/bench/runs/fault_${f}_${c}_r$ROUND --benchmark dev --config configs/bench_$c.json --fault $f --workers 4
done; done
