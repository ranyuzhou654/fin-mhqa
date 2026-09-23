"""benchmark_v1 generation: program-computed answers, opaque ids and leakage-safe splits."""
from decimal import Decimal
import itertools
import pytest
from finmhqa.bench.generate import GENERATORS, Ledger, jaccard, split, ratio_value, t3, t4, t6

VALUES = {  # company -> metric -> (current yuan, prior yuan)
    '甲': {'revenue': (200, 100), 'cost_of_revenue': (150, 80), 'net_profit': (30, 20), 'np_parent': (25, 18),
          'total_assets': (1000, 900), 'total_liabilities': (600, 500), 'cfo_net': (40, -5)},
    '乙': {'revenue': (300, 250), 'cost_of_revenue': (100, 90), 'net_profit': (50, 40), 'np_parent': (45, 30),
          'total_assets': (800, 700), 'total_liabilities': (200, 100), 'cfo_net': (10, 8)},
    '丙': {'revenue': (100, 90), 'cost_of_revenue': (60, 50), 'net_profit': (-10, 5), 'np_parent': (-8, 4),
          'total_assets': (500, 400), 'total_liabilities': (400, 300), 'cfo_net': (5, 4)},
}
BS = {'total_assets', 'total_liabilities'}


def facts():
    out = []
    for company, metrics in VALUES.items():
        for metric, (cur, pri) in metrics.items():
            periods = ('2025-06-30', '2024-12-31') if metric in BS else ('2025H1', '2024H1')
            for period, v in zip(periods, (cur, pri)):
                out.append({'fact_id': f'{company}-{metric}-{period}', 'company': company, 'scope': 'consolidated',
                            'metric': metric, 'metric_label': metric, 'period': period, 'value_yuan': str(v),
                            'unit': '元', 'unit_source_kind': 'table_header'})
    return out


def test_ratio_growth_and_bridge_answers_are_computed_from_facts():
    ledger = Ledger(facts())
    assert ratio_value(ledger, '甲', 'gross_margin')[0] == Decimal('25.00')
    assert ratio_value(ledger, '甲', 'debt_ratio')[0] == Decimal('60.00')
    growth = [c for c in t4(ledger) if c['template'] == 't4_growth' and c['facts'][0] == '甲-revenue-2025H1'][0]
    assert growth['answer']['value'] == '100.00' and growth['answer']['unit'] == '%'
    # a non-positive base (甲 cfo prior = -5) never becomes a growth question
    assert not [c for c in t4(ledger) if c['template'] == 't4_growth' and c['facts'][0] == '甲-cfo_net-2025H1']
    bridge = [c for c in t3(ledger) if '比较乙与甲' in c['question'] and '较低' in c['question'] and c['facts'][1] == '甲-revenue-2025H1'
              and c['facts'][2].endswith('np_parent-2025H1')][0]
    assert bridge['answer']['selected_company'] == '甲' and bridge['answer']['value'] == '0.00'  # 25 yuan in 亿元


def test_ids_are_opaque_and_unanswerable_items_have_no_facts():
    ledger = Ledger(facts())
    pool = [c for g in GENERATORS.values() for c in g(ledger)]
    assert all(c['id'].startswith('b1-') and 'T' not in c['id'][3:] for c in pool)
    assert len({c['id'] for c in pool}) == len(pool)
    unanswerable = t6(ledger)
    assert unanswerable and all(c['answer']['status'] == 'insufficient' and c['facts'] == [] for c in unanswerable)


def test_split_keeps_groups_apart_requires_new_test_fact_and_blocks_near_duplicates():
    ledger = Ledger(facts())
    pool = [c for g in GENERATORS.values() for c in g(ledger)]
    prior = {'甲-revenue-2025H1', '乙-revenue-2025H1', '丙-revenue-2025H1'}
    quotas = {'T1': (1, 2), 'T2': (1, 2), 'T3': (1, 2), 'T4': (1, 2), 'T5': (1, 1), 'T6': (1, 1)}
    chosen = split(pool, quotas, prior, seed=1, reserve_ratio=0)
    test, dev = chosen['test'], chosen['dev']
    group = lambda c: c.get('group') or '|'.join(sorted(c['facts']))
    assert not {group(c) for c in test} & {group(c) for c in dev}
    assert all(set(c['facts']) - prior for c in test if c['facts'])
    assert all(jaccard(d['question'], t['question']) <= 0.8 for d, t in itertools.product(dev, test))


def test_split_fails_loudly_when_quota_cannot_be_met():
    ledger = Ledger(facts())
    with pytest.raises(ValueError, match='not enough'):
        split(t6(ledger), {'T6': (0, 10_000)}, set(), seed=1)
