#!/bin/zsh
# S6: every baseline config three times on the frozen Dev split; B0/B1 first, then B2 and B3 streams in parallel.
set -u
cd "$(dirname "$0")/.."
run() {  # config, repetition
  local out=data/bench/runs/dev_$1_r$2
  [ -d $out ] && { echo "skip $out"; return; }
  .venv/bin/python scripts/run_agent_dev.py --benchmark dev --config configs/bench_$1.json --out $out --workers 4 > $out.log 2>&1
  echo "done $out exit $?"
}
for r in 1 2 3; do run b0_closed_book $r; run b1_single_retrieval $r; done
(for r in 1 2 3; do run b2_flat $r; done) &
(for r in 1 2 3; do run b3_graph $r; done) &
wait
