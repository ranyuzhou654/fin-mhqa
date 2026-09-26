#!/bin/zsh
# B3 ablation (CFG=config stem): fault runs F1-F3 (1 round) then clean Dev (3 runs), strictly one run at a time.
# Resumable per item (see scripts/lib_run.zsh); stops at once when the model quota is exhausted.
set -u
CFG=${CFG:?set CFG to a config stem}
cd "$(dirname "$0")/.."
source scripts/lib_run.zsh
cost_gate $CFG 282
canary_gate $CFG
CANARY=0 CONFIGS=$CFG ./scripts/run_fault_injection.sh || exit $?
for r in 1 2 3; do
  bench_run data/bench/runs/dev_${CFG}_r$r --benchmark dev --config configs/bench_${CFG}.json --workers 4
done
