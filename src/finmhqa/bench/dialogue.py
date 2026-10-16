"""benchmark_v2 multi-turn dialogues from the verified fact ledger (docs/benchmark/BENCHMARK_V2_DESIGN.md).

Each dialogue is a chain of 2-4 user turns. Later turns leave out what the conversation already fixed (company,
metric, period, scope) or point back at earlier answers ("两家里较高的那家", "它", "两者相差"). Every turn carries:

- utterance:   what the user types, with the ellipsis and references;
- standalone:  the same request written out in full, in the benchmark_v1 template style (the rewrite target);
- answer / facts / derivation: computed here with Decimal, as in benchmark_v1; no model is involved;
- phenomena:   which dialogue phenomena the turn needs (ellipsis, pronoun, comparison reference, correction, ...).

Answers are scored per turn against the turn's own gold, so a wrong earlier answer counts once where it happens and
again only if the next turn really inherits it (error propagation is measured, not assumed).
"""
import hashlib
from itertools import combinations, permutations
from .generate import (AMOUNT, CURRENT, PERCENT, PERIOD, PRIOR, RATIOS, STATEMENT, Ledger, amount_answer, pct,
                       percent_answer)

SHORT_AMOUNT = '（亿元，四舍五入保留两位小数）'
SHORT_PERCENT = '（百分比，四舍五入保留两位小数）'
PARENT_METRICS = ('net_profit', 'cfo_net', 'total_assets')
# Ratios of two current-period consolidated amounts, usable as "these two numbers" in a follow-up turn.
SIMPLE_RATIOS = {rid: v for rid, v in RATIOS.items() if rid != 'gross_margin'}
GROWTH_DEF = {'IS': '（同比增长率=(2025年上半年−2024年上半年)÷2024年上半年）',
              'CF': '（同比增长率=(2025年上半年−2024年上半年)÷2024年上半年）',
              'BS': '（增长率=(2025年6月30日−2024年12月31日)÷2024年12月31日）'}


def pick(options, key):
    """Deterministic phrasing variant, so the same chain does not always read the same way."""
    return options[int(hashlib.sha256(key.encode()).hexdigest(), 16) % len(options)]


def turn(utterance, standalone, answer, facts, derivation, phenomena=(), selected=None, refers_to=()):
    return {'utterance': utterance, 'standalone': standalone, 'answer': {**answer, 'selected_company': selected},
            'facts': [f['fact_id'] for f in facts], 'derivation': derivation, 'phenomena': sorted(phenomena),
            'refers_to': list(refers_to)}


def dialogue(chain, template, turns, facts):
    text = '\n'.join(t['utterance'] for t in turns)
    tags = set()
    if any(f['unit_source_kind'] != 'table_header' for f in facts):
        tags.add('hard_location')
    if any(f['unit'] != '元' for f in facts):
        tags.add('unit_conversion')
    for i, t in enumerate(turns, 1):
        t['turn'] = i
    return {'id': 'b2-' + hashlib.sha256(text.encode()).hexdigest()[:10], 'type': chain, 'template': template,
            'question': text, 'turns': turns, 'facts': sorted({f['fact_id'] for f in facts}), 'tags': sorted(tags)}


def amount_q(company, metric_label, period, scope='合并口径'):
    return f"根据半年报，{company}{PERIOD[period]}{scope}{metric_label}是多少？{AMOUNT}"


def d1_bridge(ledger):
    """A's metric -> B same period (ellipsis) -> pick the higher/lower one and ask its target metric (reference),
    optionally -> growth of that target ("它")."""
    out = []
    for a, b in permutations(ledger.companies, 2):
        for selector in ('revenue', 'net_profit', 'total_assets', 'np_parent', 'cfo_net'):
            fa, fb = ledger.get(a, selector), ledger.get(b, selector)
            if not fa or not fb or Ledger.yuan(fa) == Ledger.yuan(fb):
                continue
            period = CURRENT[STATEMENT[selector]]
            for higher in (True, False):
                chosen = fa if (Ledger.yuan(fa) > Ledger.yuan(fb)) == higher else fb
                company, word = chosen['company'], '较高' if higher else '较低'
                for target, kind in STATEMENT.items():
                    ft = ledger.get(company, target)
                    if target == selector or not ft:
                        continue
                    key = f'{a}{b}{selector}{target}{higher}'
                    t1 = turn(amount_q(a, fa['metric_label'], period), amount_q(a, fa['metric_label'], period),
                              amount_answer(Ledger.yuan(fa)), [fa], f"convert({fa['fact_id']})")
                    u2 = pick([f'{b}同期呢？{SHORT_AMOUNT}', f'那{b}呢？同一期间、同一口径。{SHORT_AMOUNT}'], key)
                    t2 = turn(u2, amount_q(b, fb['metric_label'], period), amount_answer(Ledger.yuan(fb)), [fb],
                              f"convert({fb['fact_id']})", ['ellipsis_metric', 'ellipsis_period'], refers_to=[1])
                    u3 = pick([f'两家里{word}的那家，{PERIOD[CURRENT[kind]]}{ft["metric_label"]}是多少？{SHORT_AMOUNT}',
                               f'取这两家中{word}的一家，它{PERIOD[CURRENT[kind]]}的{ft["metric_label"]}是多少？合并口径。{SHORT_AMOUNT}'], key)
                    s3 = (f"根据半年报，比较{a}与{b}{PERIOD[period]}合并口径{fa['metric_label']}，选择{word}的一家公司，"
                          f"再查询该公司{PERIOD[CURRENT[kind]]}合并口径{ft['metric_label']}是多少？{AMOUNT}")
                    t3 = turn(u3, s3, amount_answer(Ledger.yuan(ft)), [fa, fb, ft],
                              f"select_{'max' if higher else 'min'}(turn1,turn2)->convert({ft['fact_id']})",
                              ['comparison_reference', 'pronoun'], selected=company, refers_to=[1, 2])
                    turns, facts = [t1, t2, t3], [fa, fb, ft]
                    prior = ledger.get(company, target, PRIOR[kind])
                    if prior and Ledger.yuan(prior) > 0 and higher:
                        growth = pct((Ledger.yuan(ft) - Ledger.yuan(prior)) / Ledger.yuan(prior))
                        u4 = pick([f'它的增长率呢？{GROWTH_DEF[kind]}{SHORT_PERCENT}',
                                   f'这个指标比上期增长了多少？{GROWTH_DEF[kind]}{SHORT_PERCENT}'], key + 'g')
                        s4 = (f"根据半年报，{company}合并口径{ft['metric_label']}的增长率是多少？{GROWTH_DEF[kind]}{PERCENT}")
                        turns.append(turn(u4, s4, percent_answer(growth), [ft, prior],
                                          f"growth({ft['fact_id']},{prior['fact_id']})", ['pronoun', 'ellipsis_company'],
                                          selected=company, refers_to=[3]))
                        facts.append(prior)
                    out.append(dialogue('D1', 'd1_bridge' + ('_growth' if len(turns) == 4 else ''), turns, facts))
    return out


def d2_difference(ledger):
    """A's metric -> B (ellipsis) -> difference of the two previous answers."""
    out = []
    for a, b in permutations(ledger.companies, 2):
        for metric, kind in STATEMENT.items():
            fa, fb = ledger.get(a, metric), ledger.get(b, metric)
            if not fa or not fb:
                continue
            period = CURRENT[kind]
            key = f'{a}{b}{metric}'
            t1 = turn(amount_q(a, fa['metric_label'], period), amount_q(a, fa['metric_label'], period),
                      amount_answer(Ledger.yuan(fa)), [fa], f"convert({fa['fact_id']})")
            t2 = turn(pick([f'{b}呢？{SHORT_AMOUNT}', f'换成{b}，同一指标同一期间是多少？{SHORT_AMOUNT}'], key),
                      amount_q(b, fb['metric_label'], period), amount_answer(Ledger.yuan(fb)), [fb], f"convert({fb['fact_id']})",
                      ['ellipsis_metric', 'ellipsis_period'], refers_to=[1])
            t3 = turn(pick([f'前者减去后者是多少？保留正负号。{SHORT_AMOUNT}', f'两者相差多少？用{a}减{b}，保留正负号。{SHORT_AMOUNT}'], key),
                      f"根据半年报，{a}{PERIOD[period]}合并口径{fa['metric_label']}减去{b}同期同一指标，差额是多少？保留正负号，{AMOUNT}",
                      amount_answer(Ledger.yuan(fa) - Ledger.yuan(fb)), [fa, fb], 'difference(turn1,turn2)',
                      ['previous_answers'], refers_to=[1, 2])
            out.append(dialogue('D2', 'd2_difference', [t1, t2, t3], [fa, fb]))
    return out


def d3_correction(ledger):
    """Consolidated figure -> the user corrects the scope to the parent-company statement."""
    out = []
    for c in ledger.companies:
        for metric in PARENT_METRICS:
            for period in (CURRENT[STATEMENT[metric]], PRIOR[STATEMENT[metric]]):
                fc, fp = ledger.get(c, metric, period), ledger.get(c, metric, period, 'parent')
                if not fc or not fp or fc['value_yuan'] == fp['value_yuan']:
                    continue
                key = f'{c}{metric}{period}'
                q1 = f"根据半年报，{c}{PERIOD[period]}的{fc['metric_label']}是多少？合并口径。{AMOUNT}"
                t1 = turn(q1, q1, amount_answer(Ledger.yuan(fc)), [fc], f"convert({fc['fact_id']})")
                t2 = turn(pick([f'不对，我要的是母公司报表里的数，不是合并口径。{SHORT_AMOUNT}',
                                f'抱歉说错了，应该是母公司口径。{SHORT_AMOUNT}'], key),
                          f"根据{c}2025年半年度报告，{c}母公司报表（母公司口径，不是合并口径）中{PERIOD[period]}的{fp['metric_label']}是多少？{AMOUNT}",
                          amount_answer(Ledger.yuan(fp)), [fp], f"convert({fp['fact_id']})",
                          ['correction', 'ellipsis_company', 'ellipsis_metric', 'ellipsis_period'], refers_to=[1])
                out.append(dialogue('D3', 'd3_correction', [t1, t2], [fc, fp]))
    return out


def d4_ratio(ledger):
    """Same company: denominator -> numerator (ellipsis of company) -> their ratio."""
    out = []
    for c in ledger.companies:
        for rid, (num, den, name, definition) in SIMPLE_RATIOS.items():
            fn, fd = ledger.get(c, num), ledger.get(c, den)
            if not fn or not fd or Ledger.yuan(fd) <= 0:
                continue
            period = CURRENT[STATEMENT[den]]
            key = f'{c}{rid}'
            t1 = turn(amount_q(c, fd['metric_label'], period), amount_q(c, fd['metric_label'], period),
                      amount_answer(Ledger.yuan(fd)), [fd], f"convert({fd['fact_id']})")
            t2 = turn(pick([f'那{fn["metric_label"]}呢？{SHORT_AMOUNT}', f'同一家公司同期的{fn["metric_label"]}是多少？{SHORT_AMOUNT}'], key),
                      amount_q(c, fn['metric_label'], period), amount_answer(Ledger.yuan(fn)), [fn], f"convert({fn['fact_id']})",
                      ['ellipsis_company', 'ellipsis_period'], refers_to=[1])
            t3 = turn(f'用这两个数算一下{name}。（{definition}）{SHORT_PERCENT}',
                      f"根据半年报，{c}{PERIOD[period]}合并口径的{name}是多少？（{definition}）{PERCENT}",
                      percent_answer(pct(Ledger.yuan(fn) / Ledger.yuan(fd))), [fn, fd], f'{rid}(turn2,turn1)',
                      ['previous_answers'], refers_to=[1, 2])
            out.append(dialogue('D4', 'd4_ratio', [t1, t2, t3], [fd, fn]))
    return out


def d5_period(ledger):
    """Current period -> prior period (ellipsis) -> growth between the two answers."""
    out = []
    for c in ledger.companies:
        for metric, kind in STATEMENT.items():
            cur, pri = ledger.get(c, metric), ledger.get(c, metric, PRIOR[kind])
            if not cur or not pri or Ledger.yuan(pri) <= 0:
                continue
            key = f'{c}{metric}'
            when = '上年同期' if kind != 'BS' else '期初（2024年12月31日）'
            t1 = turn(amount_q(c, cur['metric_label'], CURRENT[kind]), amount_q(c, cur['metric_label'], CURRENT[kind]),
                      amount_answer(Ledger.yuan(cur)), [cur], f"convert({cur['fact_id']})")
            t2 = turn(pick([f'{when}呢？{SHORT_AMOUNT}', f'和{when}比呢，{when}是多少？{SHORT_AMOUNT}'], key),
                      amount_q(c, pri['metric_label'], PRIOR[kind]), amount_answer(Ledger.yuan(pri)), [pri],
                      f"convert({pri['fact_id']})", ['ellipsis_company', 'ellipsis_metric'], refers_to=[1])
            t3 = turn(f'增长率是多少？{GROWTH_DEF[kind]}{SHORT_PERCENT}',
                      f"根据半年报，{c}合并口径{cur['metric_label']}的增长率是多少？{GROWTH_DEF[kind]}{PERCENT}",
                      percent_answer(pct((Ledger.yuan(cur) - Ledger.yuan(pri)) / Ledger.yuan(pri))), [cur, pri],
                      'growth(turn1,turn2)', ['previous_answers', 'ellipsis_company', 'ellipsis_metric'], refers_to=[1, 2])
            out.append(dialogue('D5', 'd5_period', [t1, t2, t3], [cur, pri]))
    return out


GENERATORS = {'D1': d1_bridge, 'D2': d2_difference, 'D3': d3_correction, 'D4': d4_ratio, 'D5': d5_period}
