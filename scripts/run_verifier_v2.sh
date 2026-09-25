#!/bin/zsh
# Verifier v2 ablation: fault runs F1-F3 (1 round) then clean Dev (3 runs), strictly one run at a time; resumable.
set -u
cd "$(dirname "$0")/.."
CONFIGS=b3_graph_repair_verifier_v2 ./scripts/run_fault_injection.sh
for r in 1 2 3; do
  out=data/bench/runs/dev_b3_graph_repair_verifier_v2_r$r
  [ -d $out ] && { echo "skip $out"; continue; }
  PYTHONPATH=src .venv/bin/python scripts/run_agent_dev.py --benchmark dev --config configs/bench_b3_graph_repair_verifier_v2.json --out $out --workers 4 > $out.log 2>&1
  echo "done $out exit $?"
  PYTHONPATH=src:scripts .venv/bin/python scripts/audit_agent_run.py --run $out --out $out.audit.json
done
