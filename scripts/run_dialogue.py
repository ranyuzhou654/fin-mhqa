"""Run benchmark_v2 dialogues with the four context strategies (docs/benchmark/BENCHMARK_V2_DESIGN.md). Never opens gold.

Layout under --out:
  manifest.json
  turn1/<dialogue>/            the first turn, identical input for every strategy, run once and shared
  <S>/<dialogue>/turn_<k>/     later turns per strategy (k >= 2); S3/S4 also keep rewrite/ with the rewrite call
Each turn folder holds trajectory.jsonl, result.json and the step_NN model calls, as in run_agent_dev.py.
Resumable: finished turns (result.json present) are reused; a quota refusal stops everything with exit code 3.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import threading
import time
import urllib.error
from finmhqa.agent.conversation import (REWRITE_SCHEMA, REWRITER, STRATEGIES, CarryAgentGraph, carried_evidence,
                                        rewrite_payload, with_transcript)
from finmhqa.agent.evidence_context import EvidenceContext
from finmhqa.agent.openai_compat import OpenAICompatTeacher
from finmhqa.agent.pilot_tools import request_json, search
from finmhqa.harness.provenance import git_state, retrieval_lock
from finmhqa.harness.runs import EXIT_OK, EXIT_QUOTA, QuotaExhausted, classify_error

DIALOGUES = Path('data/bench/generated_v2')


class Stop(Exception):
    pass


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
    config = json.loads(a.config.read_text())
    spec = config['teacher']
    teacher = OpenAICompatTeacher(spec['base_url'], spec['model'], repair=bool(config.get('format_repair')))
    budgets = dict(config['budgets'], reserve_calculation=config.get('reserve_calculation', False),
                   calculator=config.get('calculator', 'v1'), **{k: config[k] for k in ('verifier', 'planner', 'context_policy') if k in config})
    path = DIALOGUES / f'{a.split}_dialogues.jsonl'
    dialogues = [json.loads(l) for l in path.read_text().splitlines()]
    if any(set(d) != {'id', 'turns'} for d in dialogues):
        raise ValueError('dialogue input must hold ids and utterances only')
    if a.only:
        dialogues = [d for d in dialogues if d['id'] == a.only]
    health = request_json(a.url + '/health')
    dataset = health['admission']['dataset_sha256']
    context = EvidenceContext(config['context_index'], dataset, config.get('context_version', 2))
    files = [Path('src/finmhqa/agent') / f for f in ('conversation.py', 'graph.py', 'graph_contracts.py', 'openai_compat.py',
                                                     'pilot_tools.py', 'evidence_context.py', 'context_policy.py')] + [Path(__file__)]
    manifest = {'split': a.split, 'dialogues_sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'config': str(a.config),
                'config_sha256': hashlib.sha256(a.config.read_bytes()).hexdigest(), 'strategies': a.strategies,
                'code_sha256': {str(f): hashlib.sha256(f.read_bytes()).hexdigest() for f in files}, 'health_before': health,
                'git': git_state(), 'only': a.only}
    if (a.out / 'manifest.json').exists():
        old = json.loads((a.out / 'manifest.json').read_text())
        for key in ('dialogues_sha256', 'config_sha256', 'code_sha256', 'health_before'):
            if old.get(key) != manifest.get(key):
                raise ValueError(f'cannot resume: {key} differs; start a new output folder')
    else:
        a.out.mkdir(parents=True)
        (a.out / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    lock, stop = threading.Lock(), threading.Event()

    def retrieve(args):
        with lock, retrieval_lock():
            for attempt in range(60):
                try:
                    return search(a.url, args, dataset)
                except urllib.error.HTTPError as exc:
                    if exc.code != 503 or attempt == 59:
                        raise
                    time.sleep(2)

    def run_turn(folder, question, carried=None):
        """One agent run; returns {'final', 'evidence' (cited chunks only), ...}. Reuses a finished folder."""
        if (folder / 'result.json').exists():
            return json.loads((folder / 'result.json').read_text())
        if folder.exists():
            folder.rename(folder.with_name(folder.name + f'.superseded_{int(time.time())}'))
        if stop.is_set():
            raise Stop()
        folder.mkdir(parents=True)
        logs, state, error, start = [], {}, None, time.monotonic()
        with (folder / 'trajectory.jsonl').open('x') as f:
            def record(e):
                logs.append(e)
                f.write(json.dumps(e, ensure_ascii=False) + '\n')
                f.flush()
                os.fsync(f.fileno())
            record({'event': 'question', 'question': question})

            def model(q, history, step, prompt, schema):
                return teacher.decide(q, history, folder / f'step_{step:02}', system=prompt, output_schema=schema)
            agent = CarryAgentGraph(model, retrieve, record, config['arm'], synthesis=config['synthesis'],
                                    context_reader=context.read, reserve_verifier=config['reserve_verifier'], **budgets)
            try:
                state = agent.run(question, carried)
            except QuotaExhausted:
                stop.set()
                raise
            except Exception as exc:
                error = f'{type(exc).__name__}: {exc}'
                record({'event': 'error', 'message': error, 'error_class': classify_error(error)})
        final = state.get('final')
        cited = {c: state['evidence'][c] for c in (final or {}).get('citations', []) if c in state.get('evidence', {})}
        result = {'question': question, 'final': final, 'error': error, 'terminal_reason': state.get('terminal_reason', 'ERROR'),
                  'seconds': time.monotonic() - start, 'carried': list(carried or {}),
                  'search_calls': sum(e['event'] == 'tool_request' and e['tool'] == 'search' for e in logs),
                  'tool_calls': sum(e['event'] == 'tool_request' for e in logs),
                  'decision_calls': sum(e['event'] == 'teacher_decision' for e in logs),
                  'usage': [e['usage'] for e in logs if e['event'] == 'teacher_decision'], 'evidence': cited}
        (folder / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
        return result

    def rewrite(folder, previous, utterance):
        out = folder / 'rewrite'
        if (out / 'decision.json').exists():
            return json.loads((out / 'decision.json').read_text())
        if out.exists():
            out.rename(out.with_name(f'rewrite.superseded_{int(time.time())}'))
        try:
            decision, _ = teacher.decide(utterance, rewrite_payload(previous, utterance), out, system=REWRITER, output_schema=REWRITE_SCHEMA)
            return decision   # decide() already saved it as decision.json
        except QuotaExhausted:
            stop.set()
            raise

    def run_dialogue(d):
        first = run_turn(a.out / 'turn1' / d['id'], d['turns'][0])
        for s in a.strategies:
            previous = [{'utterance': d['turns'][0], 'final': first['final'], 'evidence': first['evidence']}]
            for k, utterance in enumerate(d['turns'][1:], 2):
                folder = a.out / s / d['id'] / f'turn_{k}'
                carried = None
                if s == 'S1':
                    question = utterance
                elif s == 'S2':
                    question = with_transcript(previous, utterance)
                else:
                    folder.mkdir(parents=True, exist_ok=True)
                    question = rewrite(folder, previous, utterance)['standalone']
                    carried = carried_evidence(previous) if s == 'S4' else None
                result = run_turn(folder / 'agent' if s in ('S3', 'S4') else folder, question, carried)
                previous.append({'utterance': utterance, 'final': result['final'], 'evidence': result['evidence']})
        print(json.dumps({'dialogue': d['id'], 'done': True}), flush=True)

    quota = False
    with ThreadPoolExecutor(a.workers) as pool:
        for future in [pool.submit(run_dialogue, d) for d in dialogues]:
            try:
                future.result()
            except (QuotaExhausted, Stop):
                quota = True
    after = request_json(a.url + '/health')
    if after != health:
        raise ValueError('index changed during the run')
    print(json.dumps({'dialogues': len(dialogues), 'stopped_by_quota': quota}), flush=True)
    return EXIT_QUOTA if quota else EXIT_OK


if __name__ == '__main__':
    raise SystemExit(main())
