#!/bin/zsh
# Format-repair ablation: B2/B3 with format_repair, three Dev runs each, strictly one run at a time.
set -u
cd "$(dirname "$0")/.."
run() {
  local out=data/bench/runs/dev_$1_r$2
  [ -d $out ] && { echo "skip $out"; return; }
  .venv/bin/python scripts/run_agent_dev.py --benchmark dev --config configs/bench_$1.json --out $out --workers 4 > $out.log 2>&1
  echo "done $out exit $?"
}
for r in 1 2 3; do run b2_flat_repair $r; run b3_graph_repair $r; done
