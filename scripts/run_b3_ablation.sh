#!/bin/zsh
# B3 ablation (CFG=config stem): fault runs F1-F3 (1 round) then clean Dev (3 runs), strictly one run at a time; resumable.
set -u
CFG=${CFG:?set CFG to a config stem}
cd "$(dirname "$0")/.."
CONFIGS=$CFG ./scripts/run_fault_injection.sh
for r in 1 2 3; do
  out=data/bench/runs/dev_${CFG}_r$r
  [ -d $out ] && { echo "skip $out"; continue; }
  PYTHONPATH=src .venv/bin/python scripts/run_agent_dev.py --benchmark dev --config configs/bench_${CFG}.json --out $out --workers 4 > $out.log 2>&1
  echo "done $out exit $?"
  PYTHONPATH=src:scripts .venv/bin/python scripts/audit_agent_run.py --run $out --out $out.audit.json
done
