#!/bin/zsh
# Memory v1 evaluation (docs/experiments/MEMORY_V1_PLAN.md): control, forward, reverse, contamination, one at a time.
# Each run is resumable (rerun this script); a quota refusal (exit 3) stops the driver.
set -u
cd "$(dirname "$0")/.."
OUT=data/experiments/memory_v1
mkdir -p $OUT
step() {   # name, args...
  local name=$1; shift
  if [ -f $OUT/$name.done ]; then echo "skip $name"; return 0; fi
  PYTHONPATH=src .venv/bin/python scripts/run_memory.py --out $OUT/$name "$@" >> $OUT/$name.log 2>&1
  local code=$?
  echo "done $name exit $code"
  if [ $code -eq 3 ]; then echo "STOP: quota refused; rerun after it resets"; exit 3; fi
  [ $code -eq 0 ] && touch $OUT/$name.done
}
step control --order control
step forward --order forward
step reverse --order reverse
step contaminated --order forward --seed-memory-from data/bench/runs/fault_F2_b3_graph_repair_v2_planner_v2_r1
