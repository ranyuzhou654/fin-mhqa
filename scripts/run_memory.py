"""benchmark_v1 Dev with document-structure memory (docs/experiments/MEMORY_V1_PLAN.md). Never opens gold.

Questions run one at a time in a fixed order, because memory grows as they are answered:
  1. the memory proposes chunks for the question (company + metric + scope); they are re-read from the index and
     seeded as already-observed hints (CarryAgentGraph);
  2. the frozen B3v2p agent answers;
  3. a published (verifier-approved) answer's citations are written back (locations only).

  --order forward | reverse   memory on, empty at start, questions by id ascending / descending
  --order control             memory off, same runner (the paired baseline for this time period)
  --seed-memory-from RUN...   start from memory learned offline from recorded runs (e.g. F2 fault runs: the
                              contamination test), instead of empty
"""
import argparse
import hashlib
import json
from pathlib import Path
from finmhqa.agent.pilot_tools import request_json
from finmhqa.agent.turn_runner import CODE, Stop, TurnRunner
from finmhqa.bench.guard import load_questions
from finmhqa.harness.provenance import git_state
from finmhqa.harness.runs import EXIT_OK, EXIT_QUOTA, QuotaExhausted
from finmhqa.memory.doc_memory import DocMemory

MEMORY_NOTE = ('以下证据块由文档结构记忆预取：以往通过核验的答案曾在这些表格中找到本题所提公司的同名指标行。'
               '它们只是线索，不保证期间、口径和单位符合本题，使用前必须自行核对；也可以照常检索。')


def learn_from_runs(memory, runs, by_id):
    """Offline: write memory from the published answers of recorded runs (their trajectories' final events)."""
    report = {'answers': 0, 'written': 0, 'refused': 0}
    for run in runs:
        arm = json.loads((run / 'manifest.json').read_text())['arms'][0]
        for case in sorted((run / arm).iterdir()):
            if not (case / 'result.json').exists():
                continue
            result = json.loads((case / 'result.json').read_text())
            if result.get('error') or not result.get('final'):
                continue
            r = memory.learn(result['final'], f'{run.name}/{case.name}', by_id)
            report['answers'] += result['final'].get('status') == 'answered'
            report['written'] += len(r['written'])
            report['refused'] += len(r['refused'])
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--order', choices=['forward', 'reverse', 'control'], required=True)
    p.add_argument('--config', type=Path, default=Path('configs/bench_b3_graph_repair_v2_planner_v2.json'))
    p.add_argument('--seed-memory-from', type=Path, nargs='*', default=[])
    p.add_argument('--url', default='http://127.0.0.1:8100')
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    if a.order == 'control' and a.seed_memory_from:
        p.error('the control runs without memory')
    rows, bench = load_questions('dev')
    rows = sorted(rows, key=lambda r: r['id'], reverse=a.order == 'reverse')
    runner = TurnRunner(a.config, a.url)
    by_id = runner.context.by_id
    companies = sorted({c['company'] for c in by_id.values()})
    files = CODE + [Path('src/finmhqa/memory/doc_memory.py'), Path(__file__)]
    manifest = {'order': a.order, 'benchmark': bench, 'config': str(a.config),
                'config_sha256': hashlib.sha256(a.config.read_bytes()).hexdigest(),
                'seed_memory_from': [str(r) for r in a.seed_memory_from],
                'code_sha256': {str(f): hashlib.sha256(f.read_bytes()).hexdigest() for f in files},
                'health_before': runner.health, 'git': git_state()}
    resume = (a.out / 'manifest.json').exists()
    if resume:
        old = json.loads((a.out / 'manifest.json').read_text())
        for key in ('order', 'benchmark', 'config_sha256', 'seed_memory_from', 'code_sha256', 'health_before'):
            if old.get(key) != manifest.get(key):
                raise ValueError(f'cannot resume: {key} differs; start a new output folder')
    else:
        a.out.mkdir(parents=True)
    memory = None if a.order == 'control' else DocMemory(str(a.out / 'memory.sqlite'))
    if memory and not resume:
        manifest['seeded_memory'] = learn_from_runs(memory, a.seed_memory_from, by_id) if a.seed_memory_from else None
        manifest['memory_at_start'] = memory.size()
    if not resume:
        (a.out / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    quota = False
    for row in rows:
        folder = a.out / row['id']
        done = (folder / 'result.json').exists()
        hints = [] if memory is None else memory.hints(row['question'], companies)
        seeded = {cid: by_id[cid] for cid in hints} if hints and not done else None
        try:
            result = runner.run(folder, row['question'], seeded, note=MEMORY_NOTE, source='memory_hints')
        except (QuotaExhausted, Stop):
            quota = True
            break
        if memory and not done and not result.get('error'):
            learned = memory.learn(result['final'], row['id'], by_id)
            (folder / 'memory_write.json').write_text(json.dumps(learned, ensure_ascii=False, indent=2))
        print(json.dumps({'id': row['id'], 'hints': len(hints), 'searches': result['search_calls'],
                          'status': (result.get('final') or {}).get('status'), 'error': result.get('error')}, ensure_ascii=False), flush=True)
    if request_json(a.url + '/health') != runner.health:
        raise ValueError('index changed during the run')
    print(json.dumps({'order': a.order, 'stopped_by_quota': quota, 'memory': memory.size() if memory else None}), flush=True)
    return EXIT_QUOTA if quota else EXIT_OK


if __name__ == '__main__':
    raise SystemExit(main())
