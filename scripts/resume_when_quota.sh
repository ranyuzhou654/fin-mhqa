#!/bin/zsh
# Wait for the Go quota to come back (one tiny probe every 20 minutes), then resume the queued work in order:
# dialogue strategies (resumable), the qwen ambiguity judge (resumable), then the memory evaluation driver.
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
until probe; do echo "$(date '+%m-%d %H:%M') quota not back"; sleep 1200; done
echo "$(date '+%m-%d %H:%M') quota back; resuming"
PYTHONPATH=src .venv/bin/python scripts/run_dialogue.py --workers 4 --out data/experiments/dialogue_dev_v1 >> data/experiments/dialogue_dev_v1.log 2>&1
tail -1 data/experiments/dialogue_dev_v1.log | grep -q '"stopped_by_quota": false' || { echo "dialogue run stopped again"; exit 3; }
PYTHONPATH=src .venv/bin/python scripts/dialogue_quality.py judge --judge qwen --workers 3 >> data/bench/s4_v2_judge_qwen.log 2>&1
./scripts/run_memory_v1.sh >> data/experiments/memory_v1_driver.log 2>&1
echo "exit $?"
