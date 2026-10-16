"""Score a run_dialogue.py run against benchmark_v2 gold (docs/benchmark/BENCHMARK_V2_DESIGN.md). Offline.

Per turn: value and unit as in benchmark_v1 (selected company reported separately, not part of correctness).
Primary: accuracy on follow-up turns (turn >= 2), with 95% bootstrap intervals over dialogues (turns of one
dialogue are not independent). Also: rewrite slot coverage (S3/S4), error propagation, cost per turn.
"""
import argparse
import collections
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import statistics
from finmhqa.bench.generate import PERIOD, RATIOS
from finmhqa.retrieval.metrics import mean_ci, paired_diff

GOLD = Path('data/bench/generated_v2')
LEDGER = Path('data/bench/ledger_v1/facts.jsonl')
COMPARISONS = [('S1', 'S3'), ('S3', 'S4'), ('S2', 'S3'), ('S1', 'S2')]


def correct(final, answer):
    if not final or final.get('status') != 'answered':
        return False
    try:
        return (Decimal(str(final['answer_value'])) == Decimal(answer['value'])
                and final.get('answer_unit', '').replace('％', '%') == answer['unit'])
    except (InvalidOperation, KeyError, TypeError):
        return False


def required_slots(turn, facts):
    """Words the standalone question needs: the target company, metric (or ratio name), period and scope."""
    used = [facts[f] for f in turn['facts']]
    target = turn['answer']['selected_company']
    companies = {target} if target else {f['company'] for f in used}
    names = {r[2] for r in RATIOS.values() if r[2] in turn['standalone']} or {used[-1]['metric_label']}
    periods = {p for p in PERIOD.values() if p in turn['standalone']}
    scope = {'母公司'} if any(f['scope'] == 'parent' for f in used) else set()
    return companies | names | periods | scope


def load_turn(run, strategy, did, k):
    if k == 1:
        folder = run / 'turn1' / did
    else:
        folder = run / strategy / did / f'turn_{k}'
        folder = folder / 'agent' if strategy in ('S3', 'S4') else folder
    path = folder / 'result.json'
    return json.loads(path.read_text()) if path.exists() else None


def rewrite_of(run, strategy, did, k):
    path = run / strategy / did / f'turn_{k}' / 'rewrite' / 'decision.json'
    return json.loads(path.read_text())['standalone'] if path.exists() else None


def tokens(result):
    return sum(u.get('input_tokens', 0) for u in (result or {}).get('usage', []))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run', type=Path, required=True)
    p.add_argument('--split', default='dev')
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    gold = [json.loads(l) for l in (GOLD / f'{a.split}_gold.jsonl').read_text().splitlines()]
    gold = [g for g in gold if not g['reserve']]
    facts = {f['fact_id']: f for f in map(json.loads, LEDGER.read_text().splitlines())}
    strategies = json.loads((a.run / 'manifest.json').read_text())['strategies']
    rows = []
    for g in gold:
        for s in strategies:
            prev_ok = None
            for t in g['turns']:
                r = load_turn(a.run, s, g['id'], t['turn'])
                ok = correct((r or {}).get('final'), t['answer'])
                row = {'dialogue': g['id'], 'type': g['type'], 'strategy': s, 'turn': t['turn'], 'missing': r is None,
                       'correct': float(ok), 'previous_correct': prev_ok, 'phenomena': t['phenomena'],
                       'selected_ok': (None if not t['answer']['selected_company'] else
                                       ((r or {}).get('final') or {}).get('selected_company') == t['answer']['selected_company']),
                       'error': (r or {}).get('error'), 'searches': (r or {}).get('search_calls', 0),
                       'model_calls': (r or {}).get('decision_calls', 0), 'input_tokens': tokens(r),
                       'carried': len((r or {}).get('seeded', []))}
                if s in ('S3', 'S4') and t['turn'] > 1:
                    text = rewrite_of(a.run, s, g['id'], t['turn']) or ''
                    need = required_slots(t, facts)
                    row.update(rewrite=text, rewrite_missing=sorted(w for w in need if w not in text))
                    row['rewrite_complete'] = float(not row['rewrite_missing'])
                rows.append(row)
                prev_ok = ok
    follow = [r for r in rows if r['turn'] > 1]

    def per_dialogue(s, key='correct'):
        by = collections.defaultdict(list)
        for r in follow:
            if r['strategy'] == s:
                by[r['dialogue']].append(r[key])
        return [statistics.fmean(by[g['id']]) for g in gold]

    report = {'run': str(a.run), 'split': a.split, 'dialogues': len(gold), 'follow_up_turns': len(follow) // len(strategies),
              'missing_turns': sum(r['missing'] for r in rows), 'strategies': {}}
    first = [r for r in rows if r['turn'] == 1 and r['strategy'] == strategies[0]]
    report['turn1_accuracy'] = round(statistics.fmean(r['correct'] for r in first), 4)
    for s in strategies:
        fs = [r for r in follow if r['strategy'] == s]
        block = {'follow_up_accuracy': mean_ci(per_dialogue(s)),
                 'follow_up_accuracy_by_turn': round(statistics.fmean(r['correct'] for r in fs), 4),
                 'by_type': {t: round(statistics.fmean(r['correct'] for r in fs if r['type'] == t), 4) for t in sorted({r['type'] for r in fs})},
                 'by_phenomenon': {ph: round(statistics.fmean(r['correct'] for r in fs if ph in r['phenomena']), 4)
                                   for ph in sorted({ph for r in fs for ph in r['phenomena']})},
                 'selected_company_ok': [sum(r['selected_ok'] for r in fs if r['selected_ok'] is not None),
                                         sum(r['selected_ok'] is not None for r in fs)],
                 'errors': sum(bool(r['error']) for r in fs),
                 'per_follow_up_turn': {k: round(statistics.fmean(r[k] for r in fs), 3) for k in ('searches', 'model_calls', 'input_tokens', 'carried')},
                 'error_propagation': {
                     'wrong_rate_after_wrong': round(statistics.fmean(1 - r['correct'] for r in fs if r['previous_correct'] is False), 4)
                     if any(r['previous_correct'] is False for r in fs) else None,
                     'wrong_rate_after_right': round(statistics.fmean(1 - r['correct'] for r in fs if r['previous_correct'] is True), 4)}}
        if s in ('S3', 'S4'):
            block['rewrite_complete'] = round(statistics.fmean(r['rewrite_complete'] for r in fs), 4)
        report['strategies'][s] = block
    report['comparisons'] = {f'{x} -> {y}': paired_diff(per_dialogue(x), per_dialogue(y)) for x, y in COMPARISONS
                             if x in strategies and y in strategies}
    if 'S3' in strategies and 'S4' in strategies:
        s3, s4 = (statistics.fmean(r['searches'] for r in follow if r['strategy'] == s) for s in ('S3', 'S4'))
        report['H3_search_reduction'] = round(1 - s4 / s3, 4) if s3 else None
    report['turns'] = rows
    a.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: v for k, v in report.items() if k != 'turns'}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
