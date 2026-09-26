"""Replay recorded graph runs through the current code; no model calls. Exit 1 if any case diverges."""
import argparse
from collections import Counter
import json
from pathlib import Path
from finmhqa.harness.replay import replay_run


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--runs', type=Path, nargs='+', required=True)
    p.add_argument('--no-input-check', action='store_true', help='skip the byte-identical model input check')
    p.add_argument('--out', type=Path)
    a = p.parse_args()
    report = {}
    for run in a.runs:
        results = replay_run(run, check_inputs=not a.no_input_check)
        report[str(run)] = {'counts': dict(Counter(r['status'] for r in results)),
                            'diverged': [r for r in results if r['status'] == 'DIVERGED']}
        print(run, report[str(run)]['counts'])
        for r in report[str(run)]['diverged'][:3]:
            print('   ', r['id'], r['reason'][:200])
    if a.out:
        a.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    raise SystemExit(1 if any(v['diverged'] for v in report.values()) else 0)


if __name__ == '__main__':
    main()
