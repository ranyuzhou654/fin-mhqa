"""One table of every benchmark run: config, status, accuracy, error classes, tokens, provenance. No model calls.

Writes <runs-dir>/REGISTRY.json and REGISTRY.md; --snapshot also copies the Markdown table somewhere (e.g. docs/evidence).
Test runs are listed without accuracy unless --with-test, so a registry never becomes a way to peek at Test.
"""
import argparse
import json
from pathlib import Path
from finmhqa.bench.guard import GOLD
from finmhqa.bench.scoring import score_item, summarize

LEDGER = Path('data/bench/ledger_v1/facts.jsonl')

PREFIXES = ('invalid_quota_', 'invalid_', 'aborted_', 'superseded_zen_', 'superseded_')


def category(name):
    return next((p.rstrip('_') for p in PREFIXES if name.startswith(p)), 'valid')


def status(run):
    path = run / 'run_status.json'
    if path.exists():
        return json.loads(path.read_text())['status']
    return 'LEGACY' if (run / 'health_after.json').exists() else 'UNFINISHED'


def load_results(run, arm, gold, facts):
    rows = []
    for g in gold.values():
        folder = run / arm / g['id']
        if not folder.exists():
            continue   # not part of this run (fault runs cover answerable items only) or never started
        result = json.loads((folder / 'result.json').read_text()) if (folder / 'result.json').exists() else None
        attempts = [json.loads(p.read_text()) for p in sorted(folder.glob('step_*/attempts.json'))]
        if result is not None:
            result = dict(result, usage=(result.get('usage') or []) + [u for x in attempts for u in x.get('rejected_usage', [])])
        events = [json.loads(l) for l in (folder / 'trajectory.jsonl').read_text().splitlines()] if (folder / 'trajectory.jsonl').exists() else []
        rows.append(score_item(g, result, events, facts))
    return rows


def entry(run, gold, facts, with_test):
    m = json.loads((run / 'manifest.json').read_text())
    split = (m.get('benchmark') or {}).get('split')
    arm = m['arms'][0]
    row = {'run': run.name, 'category': category(run.name), 'status': status(run), 'config': m.get('config'),
           'config_sha256': (m.get('config_sha256') or '')[:12], 'arm': arm, 'split': split,
           'fault': (m.get('fault') or {}).get('type'), 'teacher': m.get('teacher'),
           'git_commit': ((m.get('git') or {}).get('commit') or '')[:10] or None, 'git_dirty': (m.get('git') or {}).get('dirty'),
           'items': sum(1 for p in (run / arm).glob('*') if p.is_dir()) if (run / arm).exists() else 0}
    if split == 'dev' or (split == 'test' and with_test):
        scores = load_results(run, arm, gold.get(split, {}), facts)
        if scores:
            s = summarize(scores)
            n = len(scores)
            row.update(accuracy=s['accuracy']['value'], scored_items=n, errors_by_class=s['errors_by_class'],
                       mean_model_calls=s['mean_model_calls'], mean_seconds=s['mean_seconds'],
                       tokens_per_item=round((s['tokens'].get('input_tokens', 0) + s['tokens'].get('output_tokens', 0)) / n))
    return row


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--runs-dir', type=Path, default=Path('data/bench/runs'))
    p.add_argument('--with-test', action='store_true')
    p.add_argument('--snapshot', type=Path)
    a = p.parse_args()
    gold = {s: {g['id']: g for g in map(json.loads, (GOLD / f'{s}_gold.jsonl').read_text().splitlines())}
            for s in ('dev', 'test') if (GOLD / f'{s}_gold.jsonl').exists()}
    facts = {f['fact_id']: f for f in map(json.loads, LEDGER.read_text().splitlines())}
    rows = [entry(r, gold, facts, a.with_test) for r in sorted(a.runs_dir.iterdir()) if (r / 'manifest.json').exists()]
    (a.runs_dir / 'REGISTRY.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2) + '\n')
    cols = ['run', 'category', 'status', 'fault', 'items', 'accuracy', 'errors_by_class', 'mean_model_calls', 'tokens_per_item', 'git_commit']
    fmt = lambda v: '' if v is None else json.dumps(v, ensure_ascii=False) if isinstance(v, dict) else str(v)
    md = '| ' + ' | '.join(cols) + ' |\n|' + '---|' * len(cols) + '\n' + ''.join('| ' + ' | '.join(fmt(r.get(c)) for c in cols) + ' |\n' for r in rows)
    (a.runs_dir / 'REGISTRY.md').write_text(md)
    if a.snapshot:
        a.snapshot.write_text('# 运行登记表快照\n\n由 `scripts/run_registry.py` 生成；Test 运行不列准确率。\n\n' + md)
    valid = [r for r in rows if r['category'] == 'valid']
    print(f"{len(rows)} runs ({len(valid)} valid); statuses {dict((s, sum(r['status'] == s for r in rows)) for s in {r['status'] for r in rows})}")


if __name__ == '__main__':
    main()
