"""Freeze benchmark_v1 (S5): question files and manifest in one directory, gold in another; never overwrites."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
from finmhqa.bench.guard import FROZEN, GOLD


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--assembled', type=Path, default=Path('data/bench/s4_v1r1/assembled'))
    p.add_argument('--spot-check', required=True, help='what the user confirmed about the 20-item Test spot check')
    a = p.parse_args()
    if FROZEN.exists() or GOLD.exists():
        raise FileExistsError('benchmark_v1 is already frozen')
    FROZEN.mkdir(parents=True); GOLD.mkdir(parents=True)
    for part in ('dev', 'test'):
        shutil.copyfile(a.assembled / f'{part}_questions.jsonl', FROZEN / f'{part}_questions.jsonl')
        shutil.copyfile(a.assembled / f'{part}_gold.jsonl', GOLD / f'{part}_gold.jsonl')
        questions = [json.loads(l) for l in (FROZEN / f'{part}_questions.jsonl').read_text().splitlines()]
        gold = [json.loads(l) for l in (GOLD / f'{part}_gold.jsonl').read_text().splitlines()]
        if [q['id'] for q in questions] != [g['id'] for g in gold] or any(set(q) != {'id', 'question'} for q in questions):
            raise ValueError(f'{part}: question file must be id+question only and aligned with gold')
    spot = (a.assembled / 'SPOT_CHECK.md').read_text()
    manifest = {
        'name': 'benchmark_v1', 'frozen_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'counts': {part: sum(1 for _ in (FROZEN / f'{part}_questions.jsonl').open()) for part in ('dev', 'test')},
        'files': {f.name: sha(f) for f in sorted(FROZEN.glob('*_questions.jsonl'))},
        'gold_sha256': {f.name: sha(f) for f in sorted(GOLD.glob('*.jsonl'))},
        'provenance_sha256': {
            'ledger_facts': sha('data/bench/ledger_v1/facts.jsonl'),
            'generation_report_r2': sha('data/bench/generated_v1r2/report.json'),
            's4_summary': sha(a.assembled / 'summary.json'),
            'spot_check_page': hashlib.sha256(spot.encode()).hexdigest(),
            **{str(p): sha(p) for p in ('src/finmhqa/bench/ledger.py', 'src/finmhqa/bench/generate.py', 'src/finmhqa/bench/quality.py',
                                        'scripts/build_fact_ledger.py', 'scripts/generate_benchmark.py', 'scripts/bench_quality.py')}},
        'spot_check': {'reviewer': 'user', 'items': 20, 'sample_seed': 20260923, 'result': a.spot_check,
                       'ids': [line.split('`')[1] for line in spot.splitlines() if line.startswith('## ')]},
        'rules': ['Dev is for tuning and architecture choice; Test only for final registered configs',
                  'Test runs require --final and a config registered in configs/benchmark_v1_registry.json',
                  'every Test run is appended to test_runs.jsonl; errors and failures stay in denominators'],
    }
    (FROZEN / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: manifest[k] for k in ('counts', 'files', 'gold_sha256')}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
