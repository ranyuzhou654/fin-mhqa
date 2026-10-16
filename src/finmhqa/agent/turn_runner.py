"""One agent run in its own folder, shared by the dialogue and memory runners.

It builds the agent from a frozen config exactly as scripts/run_agent_dev.py does (same teacher, budgets, context
reader and retrieval lock), optionally seeds already-observed evidence (CarryAgentGraph), and writes the same
trajectory.jsonl / result.json / step_NN files. A finished folder is reused; an unfinished one is set aside.
"""
import json
import os
from pathlib import Path
import threading
import time
import urllib.error
from .conversation import CARRY_NOTE, CarryAgentGraph
from .evidence_context import EvidenceContext
from .openai_compat import OpenAICompatTeacher
from .pilot_tools import request_json, search
from ..harness.provenance import retrieval_lock
from ..harness.runs import QuotaExhausted, classify_error

CODE = [Path('src/finmhqa/agent') / f for f in ('turn_runner.py', 'conversation.py', 'graph.py', 'graph_contracts.py',
                                               'openai_compat.py', 'pilot_tools.py', 'evidence_context.py', 'context_policy.py')]


class Stop(Exception):
    """Raised for work not started after a quota refusal."""


class TurnRunner:
    def __init__(self, config_path, url='http://127.0.0.1:8100'):
        self.config = json.loads(Path(config_path).read_text())
        spec = self.config['teacher']
        self.teacher = OpenAICompatTeacher(spec['base_url'], spec['model'], repair=bool(self.config.get('format_repair')))
        self.budgets = dict(self.config['budgets'], reserve_calculation=self.config.get('reserve_calculation', False),
                            calculator=self.config.get('calculator', 'v1'),
                            **{k: self.config[k] for k in ('verifier', 'planner', 'context_policy') if k in self.config})
        self.url = url
        self.health = request_json(url + '/health')
        self.dataset = self.health['admission']['dataset_sha256']
        self.context = EvidenceContext(self.config['context_index'], self.dataset, self.config.get('context_version', 2))
        self.lock, self.stop = threading.Lock(), threading.Event()

    def retrieve(self, args):
        # one inference slot: serialize threads here and processes via the file lock; wait out 503 busy replies
        with self.lock, retrieval_lock():
            for attempt in range(60):
                try:
                    return search(self.url, args, self.dataset)
                except urllib.error.HTTPError as exc:
                    if exc.code != 503 or attempt == 59:
                        raise
                    time.sleep(2)

    def decide(self, question, payload, folder, system, schema):
        """A single model call outside the graph (e.g. the dialogue rewrite); reused when already done."""
        if (folder / 'decision.json').exists():
            return json.loads((folder / 'decision.json').read_text())
        if folder.exists():
            folder.rename(folder.with_name(folder.name + f'.superseded_{int(time.time())}'))
        if self.stop.is_set():
            raise Stop()
        try:
            decision, _ = self.teacher.decide(question, payload, folder, system=system, output_schema=schema)
            return decision
        except QuotaExhausted:
            self.stop.set()
            raise

    def run(self, folder, question, seeded=None, note=CARRY_NOTE, source='carried_evidence'):
        """Run the agent on one question; returns result.json's content (with the cited chunks under 'evidence')."""
        folder = Path(folder)
        if (folder / 'result.json').exists():
            return json.loads((folder / 'result.json').read_text())
        if folder.exists():
            folder.rename(folder.with_name(folder.name + f'.superseded_{int(time.time())}'))
        if self.stop.is_set():
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
                return self.teacher.decide(q, history, folder / f'step_{step:02}', system=prompt, output_schema=schema)
            agent = CarryAgentGraph(model, self.retrieve, record, self.config['arm'], synthesis=self.config['synthesis'],
                                    context_reader=self.context.read, reserve_verifier=self.config['reserve_verifier'], **self.budgets)
            try:
                state = agent.run(question, seeded, note=note, source=source)
            except QuotaExhausted:
                self.stop.set()
                raise
            except Exception as exc:
                error = f'{type(exc).__name__}: {exc}'
                record({'event': 'error', 'message': error, 'error_class': classify_error(error)})
        final = state.get('final')
        cited = {c: state['evidence'][c] for c in (final or {}).get('citations', []) if c in state.get('evidence', {})}
        result = {'question': question, 'final': final, 'error': error, 'terminal_reason': state.get('terminal_reason', 'ERROR'),
                  'seconds': time.monotonic() - start, 'seeded': list(seeded or {}), 'seeded_from': source if seeded else None,
                  'search_calls': sum(e['event'] == 'tool_request' and e['tool'] == 'search' for e in logs),
                  'tool_calls': sum(e['event'] == 'tool_request' for e in logs),
                  'decision_calls': sum(e['event'] == 'teacher_decision' for e in logs),
                  'usage': [e['usage'] for e in logs if e['event'] == 'teacher_decision'], 'evidence': cited}
        (folder / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
        return result
