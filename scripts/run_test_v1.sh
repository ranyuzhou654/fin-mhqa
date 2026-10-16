#!/bin/zsh
# Test validation v1, phase A (docs/experiments/TEST_V1_PLAN.md): one clean Test run per registered config,
# strictly one at a time, B2 first. Canary per config; resumable; stops at once when the model quota is exhausted.
set -u
cd "$(dirname "$0")/.."
source scripts/lib_run.zsh
for CFG in b2_flat_repair b3_graph_repair_v2_planner_v2; do
  canary_gate $CFG
  bench_run data/bench/runs/test_${CFG}_r1 --benchmark test --final --config configs/bench_${CFG}.json --workers 4
done
