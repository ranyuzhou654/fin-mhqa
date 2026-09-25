"""Fault injection v1 report: B2+repair vs B3+repair under F1-F3; no model calls.

Primary: accuracy on answer-changing ("effective") plans. Misled = answered with the value the corrupted
evidence implies. Clean reference = the same items in the format-repair Dev runs.
"""
import argparse
import hashlib
import json
from pathlib import Path
import statistics
from analyze_verifier import analyze
from evaluate_benchmark import LEDGER
from finmhqa.bench.faults import FAULTS
from finmhqa.bench.guard import GOLD
from finmhqa.bench.scoring import norm_unit, paired_bootstrap, same_number, score_item, summarize

DEFAULT_CONFIGS = ['B2=b2_flat_repair', 'B3=b3_graph_repair']


def load_case(folder):
    result = json.loads((folder / 'result.json').read_text()) if (folder / 'result.json').exists() else None
    if result is not None:
        attempts = [json.loads(p.read_text()) for p in sorted(folder.glob('step_*/attempts.json'))]
        result = dict(result, usage=(result.get('usage') or []) + [u for x in attempts for u in x.get('rejected_usage', [])],
                      format_repairs=sum(len(x.get('repairs', [])) for x in attempts))
    events = [json.loads(l) for l in (folder / 'trajectory.jsonl').read_text().splitlines()] if (folder / 'trajectory.jsonl').exists() else []
    return result, events


def misled(plan, result):
    final = (result or {}).get('final') or {}
    m = plan['misled']
    return bool(plan['effective'] and m and not (result or {}).get('error') and final.get('status') == 'answered'
                and same_number(final.get('answer_value'), m['value']) and norm_unit(final.get('answer_unit')) == norm_unit(m['unit']))


def score_run(run, plans, gold, facts):
    manifest = json.loads((run / 'manifest.json').read_text())
    fault = manifest.get('fault') or {}
    if fault.get('plans_sha256') != hashlib.sha256(Path(fault['plans']).read_bytes()).hexdigest():
        raise ValueError(f'{run}: fault plans changed')
    arm = manifest['arms'][0]
    rows = []
    for plan in (p for p in plans if p['fault'] == fault['type']):
        result, events = load_case(run / arm / plan['id'])
        s = score_item(gold[plan['id']], result, events, facts)
        rows.append({**s, 'fault': fault['type'], 'effective': plan['effective'], 'misled': misled(plan, result)})
    return fault['type'], manifest['config_sha256'], rows


def rate(rows, key):
    rows = list(rows)
    return {'n': len(rows), 'value': round(sum(map(key, rows)) / len(rows), 4) if rows else None}


def block(runs):
    flat = [r for run in runs for r in run]
    eff = [r for r in flat if r['effective']]
    sums = [summarize(run) for run in runs]
    return {'runs': len(runs), 'accuracy_all': rate(flat, lambda r: r['correct']), 'accuracy_effective': rate(eff, lambda r: r['correct']),
            'misled_effective': rate(eff, lambda r: r['misled']),
            'refusal_all': rate(flat, lambda r: r['status'] == 'insufficient' and not r['error']),
            'other_wrong_effective': rate(eff, lambda r: not r['correct'] and not r['misled'] and r['status'] != 'insufficient'),
            'errors': sum(bool(r['error']) for r in flat), 'search_errors': sum(r['search_errors'] for r in flat),
            'format_repairs': sum(r['format_repairs'] for r in flat),
            'mean_model_calls': round(statistics.mean(s['mean_model_calls'] for s in sums), 3),
            'mean_tool_calls': round(statistics.mean(s['mean_tool_calls'] for s in sums), 3),
            'mean_seconds': round(statistics.mean(s['mean_seconds'] for s in sums), 2),
            'tokens_per_run': [s['tokens'] for s in sums]}


def as_correct(runs, key, ids):
    return [[{'id': r['id'], 'correct': key(r)} for r in run if r['id'] in ids] for run in runs]


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--runs-dir', type=Path, default=Path('data/bench/runs'))
    p.add_argument('--plans', type=Path, default=Path('data/bench/fault_v1/plans.jsonl'))
    p.add_argument('--configs', nargs='+', default=DEFAULT_CONFIGS, help='NAME=config stem')
    p.add_argument('--pairs', nargs='+', default=['B2:B3'], help='BASE:CANDIDATE, difference = candidate - base')
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    configs = dict(c.split('=', 1) for c in a.configs)
    pairs = [tuple(x.split(':')) for x in a.pairs]
    if a.out.exists():
        raise FileExistsError(a.out)
    plans = [json.loads(l) for l in a.plans.read_text().splitlines()]
    gold = {g['id']: g for g in map(json.loads, (GOLD / 'dev_gold.jsonl').read_text().splitlines())}
    facts = {f['fact_id']: f for f in map(json.loads, LEDGER.read_text().splitlines())}
    answerable = {p['id'] for p in plans}
    report = {'plans_sha256': hashlib.sha256(a.plans.read_bytes()).hexdigest(), 'configs': {}, 'pairs': {}, 'clean_vs_fault': {}}
    scored = {}
    for name, cfg in configs.items():
        clean = []
        for d in sorted(a.runs_dir.glob(f'dev_{cfg}_r[0-9]')):
            m = json.loads((d / 'manifest.json').read_text())
            clean.append([score_item(gold[i], *load_case(d / m['arms'][0] / i), facts) for i in sorted(answerable)])
        report['configs'][name] = {'config': f'configs/bench_{cfg}.json', 'clean_reference': {
            'runs': len(clean), 'accuracy_answerable': rate([r for run in clean for r in run], lambda r: r['correct'])}}
        scored[name] = {'clean': clean}
        for fault in FAULTS:
            dirs = sorted(a.runs_dir.glob(f'fault_{fault}_{cfg}_r[0-9]'))
            if not dirs:
                continue
            loaded = [score_run(d, plans, gold, facts) for d in dirs]
            if len({c for _, c, _ in loaded}) != 1:
                raise ValueError(f'{name} {fault}: runs use different configs')
            runs = [rows for _, _, rows in loaded]
            scored[name][fault] = runs
            entry = {'runs_dirs': [str(d) for d in dirs], **block(runs)}
            if cfg.startswith('b3'):
                entry['verifier'] = analyze(str(a.runs_dir / f'fault_{fault}_{cfg}_r[0-9]'), gold)
            report['configs'][name][fault] = entry
    for fault in FAULTS:
        eff = {p['id'] for p in plans if p['fault'] == fault and p['effective']}
        for base, cand in pairs:
            if fault not in scored[base] or fault not in scored[cand]:
                continue
            x, y = scored[base][fault], scored[cand][fault]
            report['pairs'].setdefault(f'{cand}-{base}', {})[fault] = {
                'accuracy_effective': paired_bootstrap(as_correct(x, lambda r: r['correct'], eff), as_correct(y, lambda r: r['correct'], eff)),
                'misled_effective': paired_bootstrap(as_correct(x, lambda r: r['misled'], eff), as_correct(y, lambda r: r['misled'], eff)),
                'refusal_all': paired_bootstrap(as_correct(x, lambda r: r['status'] == 'insufficient', answerable),
                                                as_correct(y, lambda r: r['status'] == 'insufficient', answerable))}
        report['clean_vs_fault'][fault] = {n: paired_bootstrap(as_correct(scored[n]['clean'], lambda r: r['correct'], eff),
                                                                as_correct(scored[n][fault], lambda r: r['correct'], eff))
                                           for n in configs if fault in scored[n] and scored[n]['clean']}
    a.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    for name, c in report['configs'].items():
        for fault in FAULTS:
            if fault in c:
                print(name, fault, 'acc_eff', c[fault]['accuracy_effective'], 'misled', c[fault]['misled_effective'],
                      'refusal', c[fault]['refusal_all'], 'errors', c[fault]['errors'])
    for pair, by_fault in report['pairs'].items():
        for fault, pr in by_fault.items():
            print(pair, fault, {k: (v['difference'], v['ci95']) for k, v in pr.items()})


if __name__ == '__main__':
    main()
