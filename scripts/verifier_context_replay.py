"""Which compact_v1 step makes the verifier ask for more cross-checks? A paired, verifier-only replay.

Under compact_v1 the verifier asked NEEDS_CROSS_CHECK at the first verification in 62/150 trajectories, against 36/150
without compaction (docs/experiments/CONTEXT_V2_PLAN.md). Whole-run comparisons mix that with diverging trajectories,
so this script freezes the inputs: it takes the first-verification payload recorded in the uncompressed B3v2p runs
(plan, candidate, history, previous rejections, budget) and shows the verifier the history in five variants:

  full      the recorded payload (re-asked, to measure the verifier's own variance)
  compact   all three compact_v1 steps
  no_drop   compact_v1 without step 1 (metadata fields are kept)
  no_repeat compact_v1 without step 2 (repeated chunks are shown in full)
  no_twin   compact_v1 without step 3 (table_kv twins are shown in full)

The variant logic is a copy of finmhqa.agent.context_policy (that module ran in recorded runs and stays unchanged).

  build  offline: reconstruct the inputs, check that 'full' is byte-identical to the recording, report sizes
  run    model calls (one verifier call per item and variant); refuses to overwrite; resumable per call
  score  offline: cross-check rate and acceptance per variant, paired against 'full'
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
from finmhqa.agent.context_policy import CHUNK_KEEP, POLICY_NOTE, REPEAT, RESPONSE_DROP, SAME_AS, compact_history
from finmhqa.agent.graph_contracts import VERIFIER_V2, VERIFY_SCHEMA_V2
from finmhqa.retrieval.metrics import paired_diff

RUNS = [Path(f'data/bench/runs/dev_b3_graph_repair_v2_planner_v2_r{r}') for r in (1, 2, 3)]
VARIANTS = {'full': None, 'compact': dict(drop=True, repeat=True, twin=True), 'no_drop': dict(drop=False, repeat=True, twin=True),
            'no_repeat': dict(drop=True, repeat=False, twin=True), 'no_twin': dict(drop=True, repeat=True, twin=False)}


def compact(items, drop, repeat, twin):
    """compact_v1 (context_policy.compact_history) with each step switchable."""
    items = deepcopy(items)
    shown, twins = set(), {}
    for x in items:
        if not isinstance(x, dict):
            continue
        for o in x.get('observations') or []:
            r = o.get('response') or {}
            if 'results' not in r:
                continue
            twins.update({(c.get('parent_table_id'), c.get('part')): c['chunk_id'] for c in r['results']
                          if c.get('chunk_type') == 'table_md' and c.get('parent_table_id')})
            new = []
            for c in r['results']:
                cid = c['chunk_id']
                pair = twins.get((c.get('parent_table_id'), c.get('part'))) if c.get('chunk_type') == 'table_kv' else None
                if repeat and cid in shown:
                    new.append({'chunk_id': cid, 'repeat': REPEAT})
                elif twin and pair and pair in shown | {m['chunk_id'] for m in r['results'] if m.get('chunk_type') == 'table_md'}:
                    new.append({'chunk_id': cid, 'chunk_type': 'table_kv', 'same_as': pair, 'note': SAME_AS})
                else:
                    new.append({k: c[k] for k in CHUNK_KEEP if k in c} if drop else c)
                    shown.add(cid)
            keep = {k: v for k, v in r.items() if k != 'results' and not (drop and k in RESPONSE_DROP)}
            o['response'] = {**keep, 'results': new}
    return items


def first_verifications():
    """(item key, question, recorded verifier payload, recorded cross-check flag) for every trajectory."""
    for run in RUNS:
        arm = json.loads((run / 'manifest.json').read_text())['arms'][0]
        for case in sorted((run / arm).iterdir()):
            if not (case / 'trajectory.jsonl').exists():
                continue
            events = [json.loads(l) for l in (case / 'trajectory.jsonl').read_text().splitlines()]
            step = next((e['step'] for e in events if e['event'] == 'teacher_decision' and e.get('role') == 'verifier'), None)
            if step is None:
                continue
            verdict = next(e for e in events if e['event'] == 'verification')
            asked = (not verdict.get('sufficient')) and any(i.get('code') == 'NEEDS_CROSS_CHECK'
                                                            for i in (verdict.get('feedback') or {}).get('issues', []))
            text = (case / f'step_{step:02}' / 'input.txt').read_text()
            if not text.startswith(VERIFIER_V2 + '\n'):
                raise ValueError(f'{case}: step {step} is not a verifier v2 input')
            body = json.loads(text[len(VERIFIER_V2) + 1:])
            yield f'{run.name}/{case.name}', body['question'], body['history'], asked, text


def variant_input(question, payload, name):
    if VARIANTS[name] is None:
        return VERIFIER_V2, payload
    return VERIFIER_V2 + POLICY_NOTE, dict(payload, history=compact(payload['history'], **VARIANTS[name]))


def build(a):
    rows, sizes = [], {v: 0 for v in VARIANTS}
    for key, question, payload, asked, recorded in first_verifications():
        for name in VARIANTS:
            system, p = variant_input(question, payload, name)
            text = system + '\n' + json.dumps({'question': question, 'history': p}, ensure_ascii=False)
            if name == 'full' and text != recorded:
                raise ValueError(f'{key}: reconstructed input differs from the recording')
            if name == 'compact' and p['history'] != compact_history(payload['history']):
                raise ValueError(f'{key}: switchable copy differs from context_policy.compact_history')
            sizes[name] += len(text)
        rows.append({'key': key, 'recorded_cross_check': asked})
    print(json.dumps({'items': len(rows), 'recorded_cross_check': sum(r['recorded_cross_check'] for r in rows),
                      'input_chars_vs_full': {v: round(s / sizes['full'], 3) for v, s in sizes.items()}}, ensure_ascii=False))


def run(a):
    from concurrent.futures import ThreadPoolExecutor
    from finmhqa.agent.openai_compat import OpenAICompatTeacher
    from finmhqa.harness.runs import QuotaExhausted
    config = json.loads(Path('configs/bench_b3_graph_repair_v2_planner_v2.json').read_text())['teacher']
    teacher = OpenAICompatTeacher(config['base_url'], config['model'], repair=True)
    a.out.mkdir(parents=True, exist_ok=True)
    tasks = []
    for key, question, payload, asked, _ in first_verifications():
        for name in a.variants:
            folder = a.out / name / key.replace('/', '__')
            if (folder / 'decision.json').exists():
                continue
            if folder.exists():   # an interrupted call: keep it aside, ask again
                folder.rename(folder.with_name(folder.name + '.interrupted'))
            tasks.append((name, key, question, payload, folder))

    def call(task):
        name, key, question, payload, folder = task
        system, p = variant_input(question, payload, name)
        teacher.decide(question, p, folder, system=system, output_schema=VERIFY_SCHEMA_V2)
        print(name, key, flush=True)
    with ThreadPoolExecutor(a.workers) as pool:
        for f in [pool.submit(call, t) for t in tasks]:
            try:
                f.result()
            except QuotaExhausted:
                print('quota exhausted; rerun to resume', flush=True)
                pool.shutdown(cancel_futures=True)
                return 3


def score(a):
    out = {}
    decisions = {}
    for name in VARIANTS:
        for f in sorted((a.out / name).glob('*/decision.json')):
            d = json.loads(f.read_text())
            decisions.setdefault(name, {})[f.parent.name] = {
                'cross_check': (not d['sufficient']) and any(i['code'] == 'NEEDS_CROSS_CHECK' for i in d['issues']),
                'accepted': bool(d['sufficient']) and not d['issues']}
    keys = sorted(set.intersection(*(set(v) for v in decisions.values())))
    for name, rows in decisions.items():
        out[name] = {'items': len(keys), 'cross_check_rate': round(sum(rows[k]['cross_check'] for k in keys) / len(keys), 4),
                     'accept_rate': round(sum(rows[k]['accepted'] for k in keys) / len(keys), 4)}
        if name != 'full':
            out[name]['cross_check_vs_full'] = paired_diff([float(decisions['full'][k]['cross_check']) for k in keys],
                                                           [float(rows[k]['cross_check']) for k in keys])
    a.report.write_text(json.dumps(out, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(out, ensure_ascii=False, indent=2))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='cmd', required=True)
    sub.add_parser('build')
    r = sub.add_parser('run')
    r.add_argument('--out', type=Path, default=Path('data/experiments/verifier_context_replay_v1'))
    r.add_argument('--variants', nargs='+', default=list(VARIANTS), choices=list(VARIANTS))
    r.add_argument('--workers', type=int, default=4)
    s = sub.add_parser('score')
    s.add_argument('--out', type=Path, default=Path('data/experiments/verifier_context_replay_v1'))
    s.add_argument('--report', type=Path, required=True)
    a = p.parse_args()
    return {'build': build, 'run': run, 'score': score}[a.cmd](a)


if __name__ == '__main__':
    sys.exit(main())
