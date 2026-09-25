"""Verifier decision analysis: were the candidates it saw right or wrong, and did it catch or block them? No model calls."""
import argparse
from collections import Counter, defaultdict
from decimal import Decimal
import glob
import json
from pathlib import Path


def correct(candidate, gold):
    answer = gold['answer']
    if answer['status'] == 'insufficient':
        return candidate.get('status') == 'insufficient'
    try:
        return (candidate.get('status') == 'answered' and Decimal(str(candidate['answer_value'])) == Decimal(answer['value'])
                and candidate.get('answer_unit', '').replace('％', '%') == answer['unit']
                and (answer['selected_company'] is None or candidate.get('selected_company') == answer['selected_company']))
    except Exception:
        return False


def analyze(pattern, gold):
    outcome, by_type, codes, items = Counter(), defaultdict(Counter), Counter(), set()
    for path in glob.glob(f'{pattern}/structured/*/trajectory.jsonl'):
        g = gold[Path(path).parent.name]
        candidate, asked = None, False
        for line in open(path):
            e = json.loads(line)
            if e['event'] == 'teacher_decision' and e['role'] == 'planner' and e['action']['action'] == 'finish':
                candidate = e['action']['final']
            elif e['event'] == 'teacher_decision' and e['role'] == 'verifier':
                asked = True
            elif e['event'] == 'verification' and asked and candidate is not None:
                key = ('accept' if e['sufficient'] else 'reject') + '_' + ('correct' if correct(candidate, g) else 'wrong')
                outcome[key] += 1
                by_type[g['type']][key] += 1
                if key == 'reject_correct':
                    items.add(g['id'])
                    codes.update(i.get('code') for i in e['feedback'].get('issues', []))
                asked = False
    return {'decisions': sum(outcome.values()), 'outcomes': dict(outcome), 'by_type': {t: dict(c) for t, c in sorted(by_type.items())},
            'false_rejection_issue_codes': dict(codes), 'items_with_false_rejection': len(items)}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--runs', nargs='+', required=True, help='glob patterns of B3-style run directories')
    p.add_argument('--gold', type=Path, default=Path('data/bench/benchmark_v1_gold/dev_gold.jsonl'))
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    gold = {g['id']: g for g in map(json.loads, a.gold.read_text().splitlines())}
    report = {pattern: analyze(pattern, gold) for pattern in a.runs}
    a.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
