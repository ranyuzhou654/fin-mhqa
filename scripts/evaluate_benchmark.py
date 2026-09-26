"""Score one or more benchmark_v1 runs of the same config; optionally compare against a baseline's runs."""
import argparse
import json
from pathlib import Path
from finmhqa.bench.guard import FROZEN, GOLD, sha256
from finmhqa.bench.scoring import score_item, summarize, across_runs, paired_bootstrap

LEDGER = Path('data/bench/ledger_v1/facts.jsonl')


def run_status(run):
    """COMPLETE/INCOMPLETE from run_status.json; runs made before the harness have none and count as LEGACY."""
    path = run / 'run_status.json'
    return json.loads(path.read_text())['status'] if path.exists() else 'LEGACY'


def load_run(run, facts, allow_incomplete=False):
    if run_status(run) == 'INCOMPLETE' and not allow_incomplete:
        raise ValueError(f'{run} is INCOMPLETE (missing or infrastructure-failed items); resume it or pass --allow-incomplete')
    manifest = json.loads((run / 'manifest.json').read_text())
    bench = manifest.get('benchmark') or {}
    split = bench.get('split')
    frozen = json.loads((FROZEN / 'manifest.json').read_text())
    if split not in ('dev', 'test') or bench.get('questions_sha256') != frozen['files'][f'{split}_questions.jsonl']:
        raise ValueError(f'{run} is not a run of the frozen benchmark_v1')
    gold_path = GOLD / f'{split}_gold.jsonl'
    if sha256(gold_path) != frozen['gold_sha256'][gold_path.name]:
        raise ValueError('gold differs from the frozen manifest')
    gold = [json.loads(l) for l in gold_path.read_text().splitlines()]
    arm = manifest['arms'][0]
    scores = []
    for g in gold:   # every frozen item is scored; a missing folder is NOT_RUN and counts as wrong
        folder = run / arm / g['id']
        result = json.loads((folder / 'result.json').read_text()) if (folder / 'result.json').exists() else None
        if result is not None:
            # rejected (malformed or repaired) model replies cost tokens too; count them and the repairs
            attempts = [json.loads(p.read_text()) for p in sorted(folder.glob('step_*/attempts.json'))]
            result = dict(result, usage=(result.get('usage') or []) + [u for x in attempts for u in x.get('rejected_usage', [])],
                          format_repairs=sum(len(x.get('repairs', [])) for x in attempts))
        events = [json.loads(l) for l in (folder / 'trajectory.jsonl').read_text().splitlines()] if (folder / 'trajectory.jsonl').exists() else []
        scores.append(score_item(g, result, events, facts))
    return split, manifest.get('config_sha256'), scores


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--runs', type=Path, nargs='+', required=True, help='repeated runs of one config')
    p.add_argument('--baseline', type=Path, nargs='*', default=[], help='runs of a baseline config for a paired comparison')
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--allow-incomplete', action='store_true', help='score INCOMPLETE runs anyway (unfinished items count as wrong)')
    a = p.parse_args()
    if a.out.exists():
        raise FileExistsError(a.out)
    facts = {f['fact_id']: f for f in map(json.loads, LEDGER.read_text().splitlines())}
    loaded = [load_run(r, facts, a.allow_incomplete) for r in a.runs]
    if len({(s, c) for s, c, _ in loaded}) != 1:
        raise ValueError('--runs must be repeated runs of one config on one split')
    runs = [s for _, _, s in loaded]
    report = {'split': loaded[0][0], 'config_sha256': loaded[0][1], 'runs': [str(r) for r in a.runs],
              'run_status': {str(r): run_status(r) for r in a.runs + a.baseline},
              'per_run': [summarize(s) for s in runs], 'across_runs': across_runs(runs),
              'scope': 'program-scored against frozen gold; errors and missing items count as wrong'}
    if a.baseline:
        base = [load_run(r, facts, a.allow_incomplete) for r in a.baseline]
        if {s for s, _, _ in base} != {report['split']}:
            raise ValueError('baseline must be on the same split')
        report['baseline'] = {'runs': [str(r) for r in a.baseline], 'config_sha256': base[0][1],
                              'across_runs': across_runs([s for _, _, s in base]),
                              'paired_difference_vs_baseline': paired_bootstrap([s for _, _, s in base], runs)}
    a.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'split': report['split'], **report['across_runs'],
                      **({'vs_baseline': report['baseline']['paired_difference_vs_baseline']} if a.baseline else {})}, ensure_ascii=False))


if __name__ == '__main__':
    main()
