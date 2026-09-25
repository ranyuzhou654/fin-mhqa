"""Net verifier effect on B3 fault runs: first planner candidate vs published outcome; no model calls."""
import argparse
from collections import Counter
from decimal import Decimal
import json
from pathlib import Path
from analyze_verifier import correct
from finmhqa.bench.guard import GOLD


def outcome(final, gold, plan):
    if not final:
        return 'error'
    if final.get('status') == 'insufficient':
        return 'refused'
    if correct(final, gold):
        return 'correct'
    m = plan['misled']
    try:
        if plan['effective'] and m and Decimal(str(final['answer_value'])) == Decimal(m['value']):
            return 'misled'
    except Exception:
        pass
    return 'other_wrong'


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--runs-dir', type=Path, default=Path('data/bench/runs'))
    p.add_argument('--plans', type=Path, default=Path('data/bench/fault_v1/plans.jsonl'))
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    gold = {g['id']: g for g in map(json.loads, (GOLD / 'dev_gold.jsonl').read_text().splitlines())}
    plans = {(q['fault'], q['id']): q for q in map(json.loads, a.plans.read_text().splitlines())}
    report = {}
    for run in sorted(a.runs_dir.glob('fault_F?_b3_graph_repair_r[0-9]')):
        fault = json.loads((run / 'manifest.json').read_text())['fault']['type']
        transitions, rejections = Counter(), Counter()
        for case in sorted((run / 'structured').iterdir()):
            plan, g = plans[(fault, case.name)], gold[case.name]
            events = [json.loads(l) for l in (case / 'trajectory.jsonl').read_text().splitlines()]
            first = next((e['action']['final'] for e in events if e['event'] == 'teacher_decision'
                          and e['role'] == 'planner' and e['action']['action'] == 'finish'), None)
            final = json.loads((case / 'result.json').read_text())['final']
            rejections[sum(e['event'] == 'verification' and not e['sufficient'] for e in events)] += 1
            transitions[f"{outcome(first, g, plan) if first else 'no_candidate'} -> {outcome(final, g, plan)}"] += 1
        report[run.name] = {'first_candidate_to_published': dict(sorted(transitions.items())),
                            'items_by_rejection_count': dict(sorted(rejections.items()))}
    a.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
