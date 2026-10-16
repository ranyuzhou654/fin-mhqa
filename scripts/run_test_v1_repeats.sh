#!/bin/zsh
# Test validation v1, phase A runs 2 and 3 (amendment of 2026-10-16 in docs/experiments/TEST_V1_PLAN.md):
# configs alternate per repeat to reduce drift between them. Resumable; stops at once when the quota is exhausted.
set -u
cd "$(dirname "$0")/.."
source scripts/lib_run.zsh
for CFG in b2_flat_repair b3_graph_repair_v2_planner_v2; do canary_gate $CFG; done
for r in 2 3; do
  for CFG in b2_flat_repair b3_graph_repair_v2_planner_v2; do
    bench_run data/bench/runs/test_${CFG}_r$r --benchmark test --final --config configs/bench_${CFG}.json --workers 4
  done
done
