"""Provider drift canary: run 5 stable Dev items with a config before a batch; no gold reaches the model.

Exit 0 = pass, 1 = drift suspected (fewer than the required correct, or an infrastructure error), 3 = quota refused.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from finmhqa.bench.guard import FROZEN, GOLD
from finmhqa.bench.scoring import score_item
from finmhqa.harness.runs import EXIT_QUOTA, INFRA

LEDGER = Path('data/bench/ledger_v1/facts.jsonl')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--config', type=Path, required=True)
    p.add_argument('--canary', type=Path, default=Path('configs/canary_v1.json'))
    p.add_argument('--out', type=Path, help='default: data/bench/canary/<time>_<config stem>')
    p.add_argument('--url', default='http://127.0.0.1:8100')
    a = p.parse_args()
    canary = json.loads(a.canary.read_text())
    out = a.out or Path('data/bench/canary') / f"{time.strftime('%Y%m%d_%H%M%S')}_{a.config.stem}"
    out.mkdir(parents=True)
    questions = {q['id']: q for q in map(json.loads, (FROZEN / 'dev_questions.jsonl').read_text().splitlines())}
    (out / 'questions.jsonl').write_text(''.join(json.dumps(questions[i], ensure_ascii=False) + '\n' for i in canary['items']))
    code = subprocess.run([sys.executable, 'scripts/run_agent_dev.py', '--questions', str(out / 'questions.jsonl'), '--config', str(a.config),
                           '--url', a.url, '--workers', '1', '--out', str(out / 'run')], env={**os.environ, 'PYTHONPATH': 'src'}).returncode
    if code == EXIT_QUOTA:
        print('canary: quota refused'); raise SystemExit(EXIT_QUOTA)
    gold = {g['id']: g for g in map(json.loads, (GOLD / 'dev_gold.jsonl').read_text().splitlines())}
    facts = {f['fact_id']: f for f in map(json.loads, LEDGER.read_text().splitlines())}
    arm = json.loads((out / 'run' / 'manifest.json').read_text())['arms'][0]
    rows = []
    for i in canary['items']:
        folder = out / 'run' / arm / i
        result = json.loads((folder / 'result.json').read_text()) if (folder / 'result.json').exists() else None
        events = [json.loads(l) for l in (folder / 'trajectory.jsonl').read_text().splitlines()] if folder.exists() else []
        s = score_item(gold[i], result, events, facts)
        rows.append({'id': i, 'type': canary['types'][i], 'correct': s['correct'], 'error_class': s['error_class'],
                     'model_calls': s['model_calls'], 'tokens': sum(s['usage'].get(k, 0) for k in ('input_tokens', 'output_tokens'))})
    correct = sum(r['correct'] for r in rows)
    infra = [r['id'] for r in rows if r['error_class'] in INFRA or r['error_class'] == 'not_run']
    need = int(canary['rule'].split('at least ')[1].split(' of')[0])
    verdict = 'PASS' if correct >= need and not infra else 'DRIFT_SUSPECTED'
    report = {'canary': canary['name'], 'config': str(a.config), 'verdict': verdict, 'correct': correct, 'required': need,
              'infra_errors': infra, 'items': rows, 'runner_exit': code}
    (out / 'canary_report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(f'canary {verdict}: {correct}/{len(rows)} correct (need {need}), infra errors {infra} -> {out}')
    raise SystemExit(0 if verdict == 'PASS' else 1)


if __name__ == '__main__':
    main()
