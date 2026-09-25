#!/bin/zsh
# Fault injection v1: B2+repair and B3+repair under F1-F3, one round, strictly one run at a time.
# Resumable: finished run dirs are skipped; move a partial dir to aborted_* before rerunning.
set -u
cd "$(dirname "$0")/.."
ROUND=${ROUND:-1}
run() {
  local out=data/bench/runs/fault_$1_$2_r$ROUND
  [ -d $out ] && { echo "skip $out"; return; }
  PYTHONPATH=src .venv/bin/python scripts/run_agent_dev.py --benchmark dev --config configs/bench_$2.json --fault $1 --out $out --workers 4 > $out.log 2>&1
  echo "done $out exit $?"
  PYTHONPATH=src:scripts .venv/bin/python scripts/audit_agent_run.py --run $out --out $out.audit.json
}
for f in F1 F2 F3; do run $f b2_flat_repair; run $f b3_graph_repair; done
