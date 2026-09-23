"""Generate benchmark_v1 Dev/Test candidates from the fact ledger (S3); no model calls."""
import argparse
from collections import Counter
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from finmhqa.agent.pilot_tools import calculate
from finmhqa.bench.generate import GENERATORS, Ledger, jaccard, split

QUOTAS = {'T1': (8, 16), 'T2': (8, 14), 'T3': (8, 14), 'T4': (8, 18), 'T5': (12, 26), 'T6': (6, 12)}
SEED = 20260923
PRIOR_SOURCES = ('data/agent_dev20_v1/gold.jsonl', 'data/synth/bridge_pilot_v1/gold.jsonl', 'data/synth/pilot_sol_v1/candidates.jsonl')
PRIOR_METRICS = {'营业收入': 'revenue', '归属于上市公司股东的净利润': 'np_parent', '经营活动产生的现金流量净额': 'cfo_net'}


def prior_facts(ledger):
    """Ledger facts already used by earlier development questions (dev20 and both pilots)."""
    used = set()
    for path in PRIOR_SOURCES:
        for line in Path(path).read_text().splitlines():
            for hop in json.loads(line)['hops']:
                metric = PRIOR_METRICS.get(hop['metric'])
                if metric is None or hop.get('period', '2025H1') != '2025H1':
                    raise ValueError(f'unmapped prior hop {hop["metric"]} {hop.get("period")}')
                fact = ledger.get(hop['company'], metric)
                if fact:
                    used.add(fact['fact_id'])
    return used


def calc(op, values, unit, digits=2):
    return calculate({'operation': op, 'values': [{'value': str(v), 'unit': '元'} for v in values],
                      'output_unit': unit, 'round_digits': digits})['value']


def recompute(c, facts):
    """Independent recomputation with the agent's calculator tool."""
    fs = [facts[i] for i in c['facts']]
    y = [Decimal(f['value_yuan']) for f in fs]
    t = c['template']
    if t.startswith('t6'):
        return None
    if t.startswith('t1'):
        return calc('convert', y, '亿元')
    if t == 't2_difference':
        return calc('difference', y, '亿元')
    if t == 't2_sum':
        return calc('sum', y, '亿元')
    if t == 't3_bridge':
        pick = max(fs[:2], key=lambda f: Decimal(f['value_yuan'])) if 'select_max' in c['derivation'] else min(fs[:2], key=lambda f: Decimal(f['value_yuan']))
        assert pick['company'] == c['answer']['selected_company'] == fs[2]['company']
        return calc('convert', y[2:], '亿元')
    if t == 't4_growth':
        return calc('growth', y, '%')
    if t == 't5_bridge_growth':
        assert max(fs[:2], key=lambda f: Decimal(f['value_yuan']))['company'] == c['answer']['selected_company'] == fs[2]['company']
        return calc('growth', y[2:], '%')
    if t == 't5_share':
        return calc('ratio', [y[0], Decimal(calc('sum', y, '元'))], '%')
    ratio_facts, ratio_values = (fs, y) if t == 't4_ratio' else (fs[3:], y[3:])
    if t == 't5_rank_ratio':
        top = max(fs[:3], key=lambda f: Decimal(f['value_yuan']))
        assert top['company'] == c['answer']['selected_company'] == ratio_facts[0]['company']
    if c['derivation'].split('(')[0].endswith('gross_margin') or '->gross_margin' in c['derivation']:
        gross = Decimal(calc('difference', ratio_values, '元'))
        return calc('ratio', [gross, ratio_values[0]], '%')
    return calc('ratio', ratio_values, '%')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--ledger', type=Path, default=Path('data/bench/ledger_v1'))
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    if a.out.exists():
        raise FileExistsError(a.out)
    facts_raw = (a.ledger / 'facts.jsonl').read_bytes()
    facts = [json.loads(l) for l in facts_raw.splitlines()]
    by_id = {f['fact_id']: f for f in facts}
    ledger = Ledger(facts)
    prior = prior_facts(ledger)
    pool = [c for gen in GENERATORS.values() for c in gen(ledger)]
    if len({c['id'] for c in pool}) != len(pool):
        raise ValueError('duplicate question text in pool')
    mismatches = []
    for c in pool:
        again = recompute(c, by_id)
        if again is not None and again != c['answer']['value']:
            mismatches.append((c['id'], c['answer']['value'], again))
    if mismatches:
        raise ValueError(f'calculator recomputation differs: {mismatches[:5]}')
    # S4 round 2 dropped more unanswerable items than the default 50% reserve could replace.
    chosen = split(pool, QUOTAS, prior, SEED, reserve_overrides={'T6': 1.0})
    main_dev = [c for c in chosen['dev'] if not c['reserve']]
    main_test = [c for c in chosen['test'] if not c['reserve']]
    # Near-duplicate check between the main Test and Dev sets.
    overlaps = []
    for t in main_test:
        for d in main_dev:
            score = jaccard(t['question'], d['question'])
            if score > 0.8:
                overlaps.append({'test': t['id'], 'dev': d['id'], 'jaccard': round(score, 3),
                                 'shared_facts': sorted(set(t['facts']) & set(d['facts']))})
    a.out.mkdir(parents=True)
    for part in ('dev', 'test'):
        (a.out / f'{part}_gold.jsonl').write_text(''.join(json.dumps(c, ensure_ascii=False) + '\n' for c in chosen[part]))
    report = {
        'seed': SEED, 'ledger_facts_sha256': hashlib.sha256(facts_raw).hexdigest(),
        'pool_by_type': dict(Counter(c['type'] for c in pool)), 'pool_by_template': dict(Counter(c['template'] for c in pool)),
        'calculator_recomputation': f'{sum(c["answer"]["status"] == "answered" for c in pool)} answered candidates match',
        'prior_facts': len(prior),
        'main': {part: {'total': len(cs), 'by_type': dict(Counter(c['type'] for c in cs)),
                        'by_template': dict(Counter(c['template'] for c in cs)),
                        'tags': dict(Counter(t for c in cs for t in c['tags'])),
                        'hard_location_share': round(sum('hard_location' in c['tags'] for c in cs) / len(cs), 3),
                        'companies': dict(Counter(f.split('|')[0] for c in cs for f in [by_id[x]['company'] for x in c['facts']] or ['(none)']))}
                 for part, cs in (('dev', main_dev), ('test', main_test))},
        'reserves': {part: sum(c['reserve'] for c in chosen[part]) for part in ('dev', 'test')},
        'test_items_with_new_fact': sum(bool(set(c['facts']) - prior) or not c['facts'] for c in main_test),
        'test_dev_group_overlap': len({'|'.join(sorted(c['facts'])) for c in main_test if c['facts']} &
                                      {'|'.join(sorted(c['facts'])) for c in main_dev if c['facts']}),
        'near_duplicates_over_0.8': overlaps,
        'code_sha256': {s: hashlib.sha256(Path(s).read_bytes()).hexdigest() for s in ('src/finmhqa/bench/generate.py', __file__)},
    }
    (a.out / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: report[k] for k in ('pool_by_type', 'calculator_recomputation', 'prior_facts', 'reserves',
                                             'test_items_with_new_fact', 'test_dev_group_overlap')}, ensure_ascii=False))
    print(json.dumps({part: {k: v for k, v in report['main'][part].items() if k != 'companies'} for part in ('dev', 'test')}, ensure_ascii=False))
    print('near duplicates > 0.8:', len(overlaps), overlaps[:3])


if __name__ == '__main__':
    main()
