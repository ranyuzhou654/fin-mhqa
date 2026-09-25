"""Precompute fault_v1 plans for the 44 answerable Dev items x F1-F3; no model calls.

Sanity gate: re-evaluating every frozen derivation without corruption must reproduce the gold answer.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from finmhqa.bench.faults import FAULTS, answer_under, build_plan
from finmhqa.bench.guard import FROZEN, GOLD, sha256

LEDGER = Path('data/bench/ledger_v1')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--index', type=Path, default=Path('index'))
    p.add_argument('--out', type=Path, default=Path('data/bench/fault_v1'))
    a = p.parse_args()
    if a.out.exists():
        raise FileExistsError(a.out)
    frozen = json.loads((FROZEN / 'manifest.json').read_text())
    gold_path = GOLD / 'dev_gold.jsonl'
    if sha256(gold_path) != frozen['gold_sha256'][gold_path.name]:
        raise ValueError('gold differs from the frozen manifest')
    gold = [json.loads(l) for l in gold_path.read_text().splitlines()]
    admitted = {f['fact_id']: f for f in map(json.loads, (LEDGER / 'facts.jsonl').read_text().splitlines())}
    reviewed = [json.loads(l) for l in (LEDGER / 'review.jsonl').read_text().splitlines()]
    data = (a.index / 'chunk_meta.jsonl').read_bytes()
    dataset = hashlib.sha256(data).hexdigest()
    chunks = {c['chunk_id']: c for c in map(json.loads, data.splitlines())}
    items = [g for g in gold if g['answer']['status'] == 'answered']
    for g in items:
        truth = {k: g['answer'][k] for k in ('value', 'unit', 'selected_company')}
        if answer_under(g, admitted, {}) != truth:
            raise ValueError(f'{g["id"]}: derivation does not reproduce gold')
    plans, skipped = [], []
    for fault in FAULTS:
        for g in items:
            try:
                plans.append({**build_plan(g, fault, chunks, admitted, reviewed), 'dataset_sha256': dataset, 'type': g['type']})
            except ValueError as e:
                skipped.append({'id': g['id'], 'fault': fault, 'reason': str(e)})
    a.out.mkdir(parents=True)
    body = ''.join(json.dumps(p, ensure_ascii=False) + '\n' for p in plans)
    (a.out / 'plans.jsonl').write_text(body)
    summary = {'plans_sha256': hashlib.sha256(body.encode()).hexdigest(), 'dataset_sha256': dataset,
               'gold_sha256': sha256(gold_path), 'items': len(items), 'plans': len(plans), 'skipped': skipped,
               'per_fault': {f: {'plans': sum(p['fault'] == f for p in plans),
                                 'effective': sum(p['fault'] == f and p['effective'] for p in plans),
                                 'misled_undefined': sum(p['fault'] == f and p['misled'] is None for p in plans),
                                 'effective_by_type': dict(Counter(p['type'] for p in plans if p['fault'] == f and p['effective']))}
                             for f in FAULTS}}
    (a.out / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
