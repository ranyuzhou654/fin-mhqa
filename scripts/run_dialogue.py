"""Run benchmark_v2 dialogues with the four context strategies (docs/benchmark/BENCHMARK_V2_DESIGN.md). Never opens gold.

Layout under --out:
  manifest.json
  turn1/<dialogue>/                 the first turn: identical input for every strategy, run once and shared
  <S>/<dialogue>/turn_<k>/          later turns (k >= 2); S3/S4 hold rewrite/ (the rewrite call) and agent/
Each agent folder holds trajectory.jsonl, result.json and step_NN model calls, as in run_agent_dev.py.
Resumable: finished turns are reused; a quota refusal stops everything with exit code 3.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
from finmhqa.agent.conversation import REWRITE_SCHEMA, REWRITER, STRATEGIES, carried_evidence, rewrite_payload, with_transcript
from finmhqa.agent.pilot_tools import request_json
from finmhqa.agent.turn_runner import CODE, Stop, TurnRunner
from finmhqa.harness.provenance import git_state
from finmhqa.harness.runs import EXIT_OK, EXIT_QUOTA, QuotaExhausted

DIALOGUES = Path('data/bench/benchmark_v2')   # frozen questions only (scripts/dialogue_quality.py freeze)


def manifest_for(a, runner, path):
    files = CODE + [Path(__file__)]
    return {'split': a.split, 'dialogues_sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'config': str(a.config),
            'config_sha256': hashlib.sha256(a.config.read_bytes()).hexdigest(), 'strategies': a.strategies,
            'code_sha256': {str(f): hashlib.sha256(f.read_bytes()).hexdigest() for f in files},
            'health_before': runner.health, 'git': git_state(), 'only': a.only}


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--split', choices=['dev'], default='dev', help='Test dialogues are not run before the Dev decision')
    p.add_argument('--config', type=Path, default=Path('configs/bench_b3_graph_repair_v2_planner_v2.json'))
    p.add_argument('--strategies', nargs='+', choices=STRATEGIES, default=list(STRATEGIES))
    p.add_argument('--url', default='http://127.0.0.1:8100')
    p.add_argument('--workers', type=int, choices=[1, 2, 4], default=4)
    p.add_argument('--only', help='one dialogue id (smoke test)')
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    path = DIALOGUES / f'{a.split}_dialogues.jsonl'
    dialogues = [json.loads(l) for l in path.read_text().splitlines()]
    if any(set(d) != {'id', 'turns'} for d in dialogues):
        raise ValueError('dialogue input must hold ids and utterances only')
    dialogues = [d for d in dialogues if not a.only or d['id'] == a.only]
    runner = TurnRunner(a.config, a.url)
    manifest = manifest_for(a, runner, path)
    if (a.out / 'manifest.json').exists():
        old = json.loads((a.out / 'manifest.json').read_text())
        for key in ('dialogues_sha256', 'config_sha256', 'code_sha256', 'health_before'):
            if old.get(key) != manifest.get(key):
                raise ValueError(f'cannot resume: {key} differs; start a new output folder')
    else:
        a.out.mkdir(parents=True)
        (a.out / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))

    def run_dialogue(d):
        first = runner.run(a.out / 'turn1' / d['id'], d['turns'][0])
        for s in a.strategies:
            previous = [{'utterance': d['turns'][0], 'final': first['final'], 'evidence': first['evidence']}]
            for k, utterance in enumerate(d['turns'][1:], 2):
                folder = a.out / s / d['id'] / f'turn_{k}'
                seeded = None
                if s == 'S1':
                    question, agent_folder = utterance, folder
                elif s == 'S2':
                    question, agent_folder = with_transcript(previous, utterance), folder
                else:
                    question = runner.decide(utterance, rewrite_payload(previous, utterance), folder / 'rewrite',
                                             REWRITER, REWRITE_SCHEMA)['standalone']
                    agent_folder = folder / 'agent'
                    seeded = carried_evidence(previous) if s == 'S4' else None
                result = runner.run(agent_folder, question, seeded)
                previous.append({'utterance': utterance, 'final': result['final'], 'evidence': result['evidence']})
        print(json.dumps({'dialogue': d['id'], 'done': True}), flush=True)

    quota = False
    with ThreadPoolExecutor(a.workers) as pool:
        for future in [pool.submit(run_dialogue, d) for d in dialogues]:
            try:
                future.result()
            except (QuotaExhausted, Stop):
                quota = True
    if request_json(a.url + '/health') != runner.health:
        raise ValueError('index changed during the run')
    print(json.dumps({'dialogues': len(dialogues), 'stopped_by_quota': quota}), flush=True)
    return EXIT_QUOTA if quota else EXIT_OK


if __name__ == '__main__':
    raise SystemExit(main())
