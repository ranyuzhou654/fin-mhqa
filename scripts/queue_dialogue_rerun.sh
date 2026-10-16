#!/bin/zsh
# After the memory driver and the round-2 judges finish: freeze benchmark_v2, then rerun the four strategies on the
# frozen Dev set (docs/benchmark/BENCHMARK_V2_DESIGN.md). One quota-heavy job at a time.
set -u
cd "$(dirname "$0")/.."
while pgrep -f "scripts/run_memory_v1.sh" >/dev/null || pgrep -f "dialogue_quality.py judge" >/dev/null; do sleep 60; done
[ -f data/bench/benchmark_v2/manifest.json ] || PYTHONPATH=src .venv/bin/python scripts/dialogue_quality.py freeze || exit 1
PYTHONPATH=src .venv/bin/python scripts/run_dialogue.py --workers 4 --out data/experiments/dialogue_dev_v2 >> data/experiments/dialogue_dev_v2.log 2>&1
echo "dialogue rerun exit $?"
