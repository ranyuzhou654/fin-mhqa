"""Build the demo cassette library: one real recorded trajectory per Dev question, with retrieval traces.

For each Dev question the first correct, error-free trajectory among the given runs is copied (trajectory,
result, and a meta.json with type and template from Dev gold). Retrieval traces are then recomputed with
scripts/backfill_retrieval_traces.py. Output goes under data/ (not in git). No model calls.
"""
import argparse
import glob
import json
from pathlib import Path
import shutil
import sys
from finmhqa.bench.guard import GOLD

sys.path.insert(0, str(Path(__file__).parent))
from analyze_verifier import correct  # noqa: E402


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--runs', nargs='+', default=['data/bench/runs/dev_b3_graph_repair_verifier_v2_r1',
                                                 'data/bench/runs/dev_b3_graph_repair_verifier_v2_r2',
                                                 'data/bench/runs/dev_b3_graph_repair_verifier_v2_r3'])
    p.add_argument('--out', type=Path, default=Path('data/service/cassettes'))
    a = p.parse_args()
    gold = {g['id']: g for g in map(json.loads, (GOLD / 'dev_gold.jsonl').read_text().splitlines())}
    a.out.mkdir(parents=True, exist_ok=True)
    picked, missing = {}, []
    for qid, g in sorted(gold.items()):
        for run in a.runs:
            arm = json.loads((Path(run) / 'manifest.json').read_text())['arms'][0]
            case = Path(run) / arm / qid
            result = json.loads((case / 'result.json').read_text())
            if not result['error'] and correct(result['final'] or {}, g):
                target = a.out / qid
                target.mkdir(exist_ok=True)
                for name in ('trajectory.jsonl', 'result.json'):
                    shutil.copy(case / name, target / name)
                rejected = sum('"event": "verification", "sufficient": false' in l for l in (case / 'trajectory.jsonl').read_text().splitlines())
                (target / 'meta.json').write_text(json.dumps({'id': qid, 'type': g['type'], 'template': g['template'], 'source_run': run,
                                                              'verifier_rejections': rejected, 'note': 'Dev 题目的真实录制轨迹'},
                                                             ensure_ascii=False, indent=2) + '\n')
                picked[qid] = run
                break
        else:
            missing.append(qid)
    print(f'{len(picked)} cassettes written to {a.out}; no correct trajectory for {missing}')


if __name__ == '__main__':
    main()
