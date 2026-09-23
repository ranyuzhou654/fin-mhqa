"""benchmark_v1 question generation from the verified fact ledger.

Every answer is computed here with Decimal from ledger facts; no model is involved. Question
text states company, metric, scope, period, unit and rounding, and defines every ratio.
"""
from decimal import Decimal, ROUND_HALF_UP
import hashlib
from itertools import combinations, permutations
import random

PERIOD = {'2025H1': '2025年上半年', '2024H1': '2024年上半年', '2025-06-30': '2025年6月30日', '2024-12-31': '2024年12月31日'}
CURRENT = {'IS': '2025H1', 'CF': '2025H1', 'BS': '2025-06-30'}
PRIOR = {'IS': '2024H1', 'CF': '2024H1', 'BS': '2024-12-31'}
AMOUNT = '金额以亿元表示，四舍五入保留两位小数。'
PERCENT = '结果以百分比表示，四舍五入保留两位小数。'
YI = Decimal(100000000)
CENT = Decimal('0.01')

# ratio id -> (numerator metric, denominator metric, name, definition). gross_margin uses (revenue - cost) / revenue.
RATIOS = {
    'net_margin': ('np_parent', 'revenue', '归母净利率', '归母净利率=归属于母公司股东的净利润÷营业收入'),
    'selling_ratio': ('selling_expense', 'revenue', '销售费用率', '销售费用率=销售费用÷营业收入'),
    'admin_ratio': ('admin_expense', 'revenue', '管理费用率', '管理费用率=管理费用÷营业收入'),
    'rd_ratio': ('rd_expense', 'revenue', '研发费用率', '研发费用率=研发费用÷营业收入'),
    'debt_ratio': ('total_liabilities', 'total_assets', '资产负债率', '资产负债率=负债合计÷资产总计'),
    'cash_ratio': ('cash', 'total_assets', '货币资金占总资产比例', '货币资金占总资产比例=货币资金÷资产总计'),
    'cfo_to_np': ('cfo_net', 'net_profit', '经营现金流与净利润之比', '经营现金流与净利润之比=经营活动产生的现金流量净额÷净利润（含少数股东损益）'),
    'gross_margin': (('revenue', 'cost_of_revenue'), 'revenue', '毛利率', '毛利率=(营业收入−营业成本)÷营业收入'),
}
STATEMENT = {'revenue': 'IS', 'cost_of_revenue': 'IS', 'selling_expense': 'IS', 'admin_expense': 'IS', 'rd_expense': 'IS',
             'net_profit': 'IS', 'np_parent': 'IS', 'cfo_net': 'CF', 'cfi_net': 'CF', 'total_assets': 'BS',
             'total_liabilities': 'BS', 'equity_parent': 'BS', 'cash': 'BS'}
BRIDGE_SELECTORS = ('revenue', 'net_profit', 'total_assets', 'np_parent', 'cfo_net')
# Companies verified absent from the corpus (no chunk mentions them).
OUTSIDE = ('山西汾酒', '长城汽车', '海信家电', '蒙牛乳业', '洋河股份')


def yi(v):
    return (v / YI).quantize(CENT, rounding=ROUND_HALF_UP)


def pct(v):
    return (v * 100).quantize(CENT, rounding=ROUND_HALF_UP)


# S4 round 1: both judges read a bare consolidated 净利润 as possibly 归母净利润; name the line item explicitly.
# The parent-company statement has no minority interest, so its 净利润 stays unqualified.
CONSOLIDATED_LABEL = {'net_profit': '净利润（含少数股东损益）'}


def label(fact):
    return CONSOLIDATED_LABEL.get(fact['metric'], fact['metric_label']) if fact['scope'] == 'consolidated' else fact['metric_label']


class Ledger:
    def __init__(self, facts):
        facts = [dict(f, metric_label=label(f)) for f in facts]
        self.by_key = {(f['company'], f['scope'], f['metric'], f['period']): f for f in facts}
        self.companies = sorted({f['company'] for f in facts})

    def get(self, company, metric, period=None, scope='consolidated'):
        return self.by_key.get((company, scope, metric, period or CURRENT[STATEMENT[metric]]))

    @staticmethod
    def yuan(f):
        return Decimal(f['value_yuan'])


def ratio_value(ledger, company, rid, period=None):
    """(value %, facts used, calculator steps) for a single-company ratio, or None."""
    num, den, _, _ = RATIOS[rid]
    if rid == 'gross_margin':
        rev, cost = ledger.get(company, 'revenue', period), ledger.get(company, 'cost_of_revenue', period)
        if not rev or not cost or ledger.yuan(rev) <= 0:
            return None
        gross = ledger.yuan(rev) - ledger.yuan(cost)
        return pct(gross / ledger.yuan(rev)), [rev, cost], [('difference', [rev, cost]), ('ratio', ['gross', rev])]
    n, d = ledger.get(company, num, period), ledger.get(company, den, period)
    if not n or not d or ledger.yuan(d) <= 0:
        return None
    return pct(ledger.yuan(n) / ledger.yuan(d)), [n, d], [('ratio', [n, d])]


def candidate(qtype, template, question, facts, answer, derivation, selected=None, extra_tags=()):
    tags = set(extra_tags)
    if any(f['unit_source_kind'] != 'table_header' for f in facts):
        tags.add('hard_location')   # value sits in a continuation table or needs a report-level unit
    if any(f['unit'] != '元' for f in facts):
        tags.add('unit_conversion')
    return {'id': 'b1-' + hashlib.sha256(question.encode()).hexdigest()[:10], 'type': qtype, 'template': template,
            'question': question, 'answer': {**answer, 'selected_company': selected},
            'facts': [f['fact_id'] for f in facts], 'derivation': derivation, 'tags': sorted(tags)}


def amount_answer(v):
    return {'status': 'answered', 'value': str(yi(v)), 'unit': '亿元', 'round_digits': 2}


def percent_answer(v):
    return {'status': 'answered', 'value': str(v), 'unit': '%', 'round_digits': 2}


def t1(ledger):
    out = []
    for c in ledger.companies:
        for metric in ('net_profit', 'cfo_net', 'total_assets'):
            for period in (CURRENT[STATEMENT[metric]], PRIOR[STATEMENT[metric]]):
                f = ledger.get(c, metric, period, 'parent')
                if f:
                    q = f"根据{c}2025年半年度报告，{c}母公司报表（母公司口径，不是合并口径）中{PERIOD[period]}的{f['metric_label']}是多少？{AMOUNT}"
                    out.append(candidate('T1', 't1_parent', q, [f], amount_answer(Ledger.yuan(f)), f"convert({f['fact_id']})", extra_tags=['parent_scope']))
        for metric, kind in STATEMENT.items():
            f = ledger.get(c, metric, PRIOR[kind])
            if f:
                when = '上年同期' if kind != 'BS' else '本报告期期初'
                q = f"根据{c}2025年半年度报告，{c}{PERIOD[PRIOR[kind]]}（{when}）的合并口径{f['metric_label']}是多少？{AMOUNT}"
                out.append(candidate('T1', 't1_prior', q, [f], amount_answer(Ledger.yuan(f)), f"convert({f['fact_id']})", extra_tags=['prior_period']))
            f = ledger.get(c, metric)
            if f and (f['unit'] != '元' or f['unit_source_kind'] != 'table_header'):
                q = f"根据{c}2025年半年度报告，{c}{PERIOD[CURRENT[kind]]}的合并口径{f['metric_label']}是多少？{AMOUNT}"
                out.append(candidate('T1', 't1_unit', q, [f], amount_answer(Ledger.yuan(f)), f"convert({f['fact_id']})"))
    return out


def t2(ledger):
    out = []
    for a, b in permutations(ledger.companies, 2):
        for metric, kind in STATEMENT.items():
            fa, fb = ledger.get(a, metric), ledger.get(b, metric)
            if not fa or not fb:
                continue
            label, when = fa['metric_label'], PERIOD[CURRENT[kind]]
            q = f"根据半年报，{a}{when}合并口径{label}减去{b}同期同一指标，差额是多少？保留正负号，{AMOUNT}"
            out.append(candidate('T2', 't2_difference', q, [fa, fb], amount_answer(Ledger.yuan(fa) - Ledger.yuan(fb)),
                                 f"difference({fa['fact_id']},{fb['fact_id']})"))
            if a < b:
                q = f"根据半年报，{a}与{b}{when}合并口径{label}两者之和是多少？{AMOUNT}"
                out.append(candidate('T2', 't2_sum', q, [fa, fb], amount_answer(Ledger.yuan(fa) + Ledger.yuan(fb)),
                                     f"sum({fa['fact_id']},{fb['fact_id']})"))
    return out


def t3(ledger):
    out = []
    for a, b in combinations(ledger.companies, 2):
        for selector in BRIDGE_SELECTORS:
            fa, fb = ledger.get(a, selector), ledger.get(b, selector)
            if not fa or not fb or Ledger.yuan(fa) == Ledger.yuan(fb):
                continue
            for higher in (True, False):
                chosen = (fa if (Ledger.yuan(fa) > Ledger.yuan(fb)) == higher else fb)
                company = chosen['company']
                for target, kind in STATEMENT.items():
                    if target == selector:
                        continue
                    ft = ledger.get(company, target)
                    if not ft:
                        continue
                    word = '较高' if higher else '较低'
                    q = (f"根据半年报，比较{a}与{b}{PERIOD[CURRENT[STATEMENT[selector]]]}合并口径{fa['metric_label']}，选择{word}的一家公司，"
                         f"再查询该公司{PERIOD[CURRENT[kind]]}合并口径{ft['metric_label']}是多少？{AMOUNT}")
                    out.append(candidate('T3', 't3_bridge', q, [fa, fb, ft], amount_answer(Ledger.yuan(ft)),
                                         f"select_{'max' if higher else 'min'}({fa['fact_id']},{fb['fact_id']})->convert({ft['fact_id']})", selected=company))
    return out


def t4(ledger):
    out = []
    for c in ledger.companies:
        for rid, (_, _, name, definition) in RATIOS.items():
            r = ratio_value(ledger, c, rid)
            if r:
                value, facts, steps = r
                when = PERIOD['2025-06-30'] if rid in ('debt_ratio', 'cash_ratio') else PERIOD['2025H1']
                q = f"根据半年报，{c}{when}合并口径的{name}是多少？（{definition}）{PERCENT}"
                out.append(candidate('T4', 't4_ratio', q, facts, percent_answer(value), f'{rid}({",".join(f["fact_id"] for f in facts)})'))
        for metric, kind in STATEMENT.items():
            cur, pri = ledger.get(c, metric), ledger.get(c, metric, PRIOR[kind])
            if not cur or not pri or Ledger.yuan(pri) <= 0:
                continue
            growth = pct((Ledger.yuan(cur) - Ledger.yuan(pri)) / Ledger.yuan(pri))
            if kind == 'BS':
                q = (f"根据半年报，{c}2025年6月30日合并口径{cur['metric_label']}较2024年12月31日的增长率是多少？"
                     f"（增长率=(期末−期初)÷期初）{PERCENT}")
            else:
                q = (f"根据半年报，{c}2025年上半年合并口径{cur['metric_label']}的同比增长率是多少？"
                     f"（同比增长率=(2025年上半年−2024年上半年)÷2024年上半年）{PERCENT}")
            out.append(candidate('T4', 't4_growth', q, [cur, pri], percent_answer(growth), f"growth({cur['fact_id']},{pri['fact_id']})"))
    return out


def t5(ledger):
    out = []
    for trio in combinations(ledger.companies, 3):
        for selector in BRIDGE_SELECTORS:
            fs = [ledger.get(c, selector) for c in trio]
            if not all(fs) or len({Ledger.yuan(f) for f in fs}) < 3:
                continue
            top = max(fs, key=Ledger.yuan)
            label, when = fs[0]['metric_label'], PERIOD[CURRENT[STATEMENT[selector]]]
            names = '、'.join(trio)
            for rid, (_, _, name, definition) in RATIOS.items():
                r = ratio_value(ledger, top['company'], rid)
                if r:
                    value, facts, _ = r
                    ratio_when = PERIOD['2025-06-30'] if rid in ('debt_ratio', 'cash_ratio') else PERIOD['2025H1']
                    q = (f"根据半年报，在{names}三家公司中，找出{when}合并口径{label}最高的公司，"
                         f"再计算该公司{ratio_when}合并口径的{name}。（{definition}）{PERCENT}")
                    out.append(candidate('T5', 't5_rank_ratio', q, fs + facts, percent_answer(value),
                                         f"select_max({','.join(f['fact_id'] for f in fs)})->{rid}", selected=top['company']))
            total = sum(Ledger.yuan(f) for f in fs)
            if total > 0 and all(Ledger.yuan(f) > 0 for f in fs):
                q = (f"根据半年报，{names}三家公司{when}合并口径{label}合计中，{trio[0]}所占比例是多少？"
                     f"（占比={trio[0]}÷三家合计）{PERCENT}")
                out.append(candidate('T5', 't5_share', q, fs, percent_answer(pct(Ledger.yuan(fs[0]) / total)),
                                     f"ratio({fs[0]['fact_id']},sum({','.join(f['fact_id'] for f in fs)}))"))
    for a, b in combinations(ledger.companies, 2):
        for selector in BRIDGE_SELECTORS:
            fa, fb = ledger.get(a, selector), ledger.get(b, selector)
            if not fa or not fb or Ledger.yuan(fa) == Ledger.yuan(fb):
                continue
            chosen = max((fa, fb), key=Ledger.yuan)['company']
            for metric, kind in STATEMENT.items():
                cur, pri = ledger.get(chosen, metric), ledger.get(chosen, metric, PRIOR[kind])
                if metric == selector or not cur or not pri or Ledger.yuan(pri) <= 0:
                    continue
                growth = pct((Ledger.yuan(cur) - Ledger.yuan(pri)) / Ledger.yuan(pri))
                growth_text = ('2025年6月30日较2024年12月31日的增长率（增长率=(期末−期初)÷期初）' if kind == 'BS'
                               else '2025年上半年的同比增长率（同比增长率=(2025年上半年−2024年上半年)÷2024年上半年）')
                q = (f"根据半年报，比较{a}与{b}{PERIOD[CURRENT[STATEMENT[selector]]]}合并口径{fa['metric_label']}，选择较高的一家公司，"
                     f"计算该公司合并口径{cur['metric_label']}{growth_text}。{PERCENT}")
                out.append(candidate('T5', 't5_bridge_growth', q, [fa, fb, cur, pri], percent_answer(growth),
                                     f"select_max({fa['fact_id']},{fb['fact_id']})->growth({cur['fact_id']},{pri['fact_id']})", selected=chosen))
    return out


def t6(ledger):
    """Unanswerable: premises verified absent from the corpus (see BENCHMARK_S3 notes)."""
    out = []
    labels = {'revenue': '营业收入', 'net_profit': CONSOLIDATED_LABEL['net_profit'], 'cfo_net': '经营活动产生的现金流量净额'}
    none = {'status': 'insufficient', 'value': '', 'unit': '', 'round_digits': None}
    for c in ledger.companies:
        for metric, label in labels.items():
            for template, q in (
                ('t6_q3', f"根据半年报，{c}2025年第三季度（7—9月）合并口径{label}是多少？{AMOUNT}"),
                ('t6_2023', f"根据半年报，{c}2023年上半年合并口径{label}是多少？{AMOUNT}"),
                ('t6_fy2025', f"根据半年报，{c}2025年全年合并口径{label}是多少？{AMOUNT}"),
            ):
                out.append({'id': 'b1-' + hashlib.sha256(q.encode()).hexdigest()[:10], 'type': 'T6', 'template': template, 'question': q,
                            'answer': {**none, 'selected_company': None}, 'facts': [], 'derivation': f'unanswerable:{template}',
                            'tags': ['unanswerable'], 'group': f'{template}|{c}'})
            for outside in OUTSIDE:
                q = f"根据半年报，{c}2025年上半年合并口径{label}减去{outside}同期同一指标，差额是多少？保留正负号，{AMOUNT}"
                out.append({'id': 'b1-' + hashlib.sha256(q.encode()).hexdigest()[:10], 'type': 'T6', 'template': 't6_outside', 'question': q,
                            'answer': {**none, 'selected_company': None}, 'facts': [], 'derivation': f'unanswerable:outside:{outside}',
                            'tags': ['unanswerable'], 'group': f't6_outside|{c}|{outside}'})
    return out


GENERATORS = {'T1': t1, 'T2': t2, 'T3': t3, 'T4': t4, 'T5': t5, 'T6': t6}


def trigrams(text):
    return {text[i:i + 3] for i in range(len(text) - 2)}


def jaccard(a, b):
    a, b = trigrams(a), trigrams(b)
    return len(a & b) / len(a | b) if a | b else 0.0


def split(candidates, quotas, prior_facts, seed, reserve_ratio=0.5, fact_cap=5, max_jaccard=0.8, reserve_overrides=None):
    """Assign candidates to test/dev (+ ordered reserves) by fact-set group.

    Test items need at least one fact never used by earlier development questions. A fact-set
    group never spans both splits; each fact appears at most `fact_cap` times per split.
    Template-level diversity: round-robin over templates within a type."""
    rng = random.Random(seed)
    group_split, usage, chosen = {}, {'test': {}, 'dev': {}}, {'test': [], 'dev': []}
    by_type = {}
    for c in candidates:
        by_type.setdefault(c['type'], []).append(c)
    for qtype, (n_dev, n_test) in quotas.items():
        pool = by_type[qtype][:]
        rng.shuffle(pool)
        templates = {}
        for c in pool:
            templates.setdefault(c['template'], []).append(c)
        order = []
        while any(templates.values()):
            for t in sorted(templates):
                if templates[t]:
                    order.append(templates[t].pop())
        for part, need in (('test', n_test), ('dev', n_dev)):
            limit = need + int(round(need * (reserve_overrides or {}).get(qtype, reserve_ratio)))
            taken = 0
            for c in order:
                if taken >= limit:
                    break
                group = c.get('group') or '|'.join(sorted(c['facts']))
                if group_split.get(group, part) != part or c.get('assigned'):
                    continue
                if part == 'test' and c['facts'] and not set(c['facts']) - prior_facts:
                    continue
                if any(usage[part].get(f, 0) >= fact_cap for f in c['facts']):
                    continue
                # Dev is filled after Test: skip near-duplicates of any Test question (character trigram Jaccard).
                if part == 'dev' and any(jaccard(c['question'], t['question']) > max_jaccard for t in chosen['test']):
                    continue
                group_split[group] = part
                for f in c['facts']:
                    usage[part][f] = usage[part].get(f, 0) + 1
                c['assigned'] = True
                chosen[part].append({**{k: v for k, v in c.items() if k != 'assigned'}, 'split': part,
                                     'rank': taken, 'reserve': taken >= need})
                taken += 1
            if taken < need:
                raise ValueError(f'not enough {qtype} candidates for {part}: {taken}/{need}')
    return chosen
