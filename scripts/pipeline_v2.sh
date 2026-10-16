#!/bin/zsh
# Quota-tolerant pipeline for the remaining work, one quota-heavy step at a time:
#   memory evaluation -> qwen judge (round 2) -> freeze benchmark_v2 -> four strategies on frozen Dev.
# Every step is resumable; when a step stops on the Go usage cap it waits (probe every 20 min) and runs it again.
set -u
cd "$(dirname "$0")/.."
probe() {
  set -a; . ./.env; set +a
  PYTHONPATH=src .venv/bin/python - <<'PY'
import os, json, sys, urllib.request, urllib.error, uuid
req = urllib.request.Request('https://opencode.ai/zen/go/v1/chat/completions',
    data=json.dumps({'model': 'deepseek-v4.1-flash', 'messages': [{'role': 'user', 'content': 'OK'}], 'max_tokens': 3}).encode(),
    headers={'Content-Type': 'application/json', 'User-Agent': 'fin-mhqa/0.1', 'x-opencode-session': 'probe-' + uuid.uuid4().hex[:12],
             'Authorization': 'Bearer ' + os.environ['LLM_API_KEY']})
try:
    urllib.request.urlopen(req, timeout=60); sys.exit(0)
except urllib.error.HTTPError as e:
    sys.exit(3 if e.code in (402, 429) else 1)
PY
}
wait_quota() { until probe; do echo "$(date '+%m-%d %H:%M') quota not back"; sleep 1200; done; }
judged() { [ $(ls data/bench/s4_v2r2/judge_qwen 2>/dev/null | wc -l) -ge 232 ]; }
run() {   # name, done-test command, step command
  local name=$1 done_test=$2 cmd=$3
  until eval $done_test; do
    wait_quota
    echo "$(date '+%m-%d %H:%M') run $name"
    eval $cmd
  done
  echo "$(date '+%m-%d %H:%M') $name complete"
}
run memory "[ -f data/experiments/memory_v1/contaminated.done ]" "./scripts/run_memory_v1.sh >> data/experiments/memory_v1_driver.log 2>&1"
run judge judged "PYTHONPATH=src .venv/bin/python scripts/dialogue_quality.py judge --judge qwen --workers 2 >> data/bench/s4_v2r2_judge_qwen.log 2>&1"
[ -f data/bench/benchmark_v2/manifest.json ] || PYTHONPATH=src .venv/bin/python scripts/dialogue_quality.py freeze >> data/bench/s4_v2r2_freeze.log 2>&1 || { echo "freeze failed"; exit 1; }
run dialogue "tail -1 data/experiments/dialogue_dev_v2.log 2>/dev/null | grep -q '\"stopped_by_quota\": false'" \
    "PYTHONPATH=src .venv/bin/python scripts/run_dialogue.py --workers 4 --out data/experiments/dialogue_dev_v2 >> data/experiments/dialogue_dev_v2.log 2>&1"
echo "$(date '+%m-%d %H:%M') pipeline complete"
