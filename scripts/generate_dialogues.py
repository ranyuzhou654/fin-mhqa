"""benchmark_v2 (multi-turn) candidates: generate dialogue chains from the fact ledger and split Dev/Test.

No model calls. Writes, under --out (refuses to overwrite):
  dev_gold.jsonl, test_gold.jsonl     full dialogues with per-turn answers (Test gold stays out of git)
  dev_dialogues.jsonl, test_dialogues.jsonl   id + per-turn utterances only (what a runner may read)
  report.json                          pool sizes, quotas, phenomena counts, overlap with benchmark_v1, hashes
"""
import argparse
import collections
import hashlib
import json
from pathlib import Path
from finmhqa.agent.pilot_tools import calculate
from finmhqa.bench.dialogue import GENERATORS
from finmhqa.bench.generate import Ledger, split

LEDGER = Path('data/bench/ledger_v1/facts.jsonl')
V1_GOLD = Path('data/bench/benchmark_v1_gold')
QUOTAS = {'D1': (8, 16), 'D2': (5, 10), 'D3': (4, 8), 'D4': (4, 8), 'D5': (5, 10)}   # (dev, test) dialogues
SEED = 20261016


def recompute(turn, facts):
    """Independent check of a turn's answer with the agent's own calculator."""
    values = [{'value': facts[i]['value_yuan'], 'unit': '元'} for i in turn['facts']]
    d = turn['derivation']
    if d.startswith('convert') or '->convert' in d:
        args = dict(operation='convert', values=values[-1:], output_unit='亿元')
    elif d.startswith('difference'):
        args = dict(operation='difference', values=values, output_unit='亿元')
    elif d.startswith('growth'):
        args = dict(operation='growth', values=values, output_unit='%')
    else:
        args = dict(operation='ratio', values=values, output_unit='%')
    r = calculate(dict(args, round_digits=2))
    return r['value'] == turn['answer']['value'] and r['unit'] == turn['answer']['unit']


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    a.out.mkdir(parents=True, exist_ok=False)
    facts = [json.loads(l) for l in LEDGER.read_text().splitlines()]
    by_id = {f['fact_id']: f for f in facts}
    ledger = Ledger(facts)
    pool = [d for g in GENERATORS.values() for d in g(ledger)]
    if not all(recompute(t, by_id) for d in pool for t in d['turns']):
        raise ValueError('an answer differs from the calculator recomputation')
    chosen = split(pool, QUOTAS, prior_facts=set(), seed=SEED, reserve_ratio=0.5)
    for part in ('dev', 'test'):
        rows = chosen[part]
        (a.out / f'{part}_gold.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows))
        main_rows = [r for r in rows if not r['reserve']]
        (a.out / f'{part}_dialogues.jsonl').write_text(''.join(
            json.dumps({'id': r['id'], 'turns': [t['utterance'] for t in r['turns']]}, ensure_ascii=False) + '\n' for r in main_rows))
    v1 = {part: {f for l in (V1_GOLD / f'{part}_gold.jsonl').read_text().splitlines() for f in json.loads(l)['facts']}
          for part in ('dev', 'test')}
    report = {'seed': SEED, 'quotas_dev_test': QUOTAS, 'pool': dict(collections.Counter(d['type'] for d in pool)),
              'turns_recomputed': sum(len(d['turns']) for d in pool)}
    for part in ('dev', 'test'):
        rows = [r for r in chosen[part] if not r['reserve']]
        used = {f for r in rows for f in r['facts']}
        report[part] = {'dialogues': len(rows), 'turns': sum(len(r['turns']) for r in rows),
                        'by_type': dict(collections.Counter(r['type'] for r in rows)),
                        'by_template': dict(collections.Counter(r['template'] for r in rows)),
                        'phenomena': dict(collections.Counter(p for r in rows for t in r['turns'] for p in t['phenomena'])),
                        'tags': dict(collections.Counter(t for r in rows for t in r['tags'])),
                        'facts': len(used), 'facts_shared_with_v1_dev': len(used & v1['dev']),
                        'facts_shared_with_v1_test': len(used & v1['test'])}
    dev_facts = {f for r in chosen['dev'] for f in r['facts']}
    test_groups = {'|'.join(sorted(r['facts'])) for r in chosen['test']}
    report['dev_test_shared_fact_groups'] = len({'|'.join(sorted(r['facts'])) for r in chosen['dev']} & test_groups)
    report['dev_test_shared_facts'] = len(dev_facts & {f for r in chosen['test'] for f in r['facts']})
    report['sha256'] = {n: hashlib.sha256((a.out / n).read_bytes()).hexdigest()
                        for n in ('dev_gold.jsonl', 'test_gold.jsonl', 'dev_dialogues.jsonl', 'test_dialogues.jsonl')}
    (a.out / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: report[k] for k in ('dev', 'test', 'dev_test_shared_fact_groups', 'dev_test_shared_facts')}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
