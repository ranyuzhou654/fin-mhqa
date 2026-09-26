"""Agent adapters for the service.

GraphAgent runs the real LangGraph agent from a frozen config (needs the retrieval server and model quota).
CassetteAgent replays recorded trajectories with their recorded timings (optionally sped up); it exercises the
whole service path (queue, workers, events, SSE, cancellation) without model calls, for tests and load tests.
"""
import json
from pathlib import Path
import threading
import time


def interruptible_sleep(seconds, cancelled, step=0.05):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cancelled():
            return
        time.sleep(min(step, max(0, end - time.monotonic())))


class CassetteAgent:
    def __init__(self, cases, speed=1.0, max_delay=None):
        """cases: folders with trajectory.jsonl (+ result.json). speed: 0.1 = ten times faster than recorded."""
        self.cases = []
        for folder in cases:
            events = [json.loads(l) for l in (Path(folder) / 'trajectory.jsonl').read_text().splitlines()]
            self.cases.append(events)
        if not self.cases:
            raise ValueError('no cassettes')
        self.by_question = {c[0]['question']: c for c in self.cases}
        self.speed, self.max_delay = speed, max_delay
        self.lock = threading.Lock()
        self.next = 0

    def pick(self, question):
        if question in self.by_question:
            return self.by_question[question]
        with self.lock:
            case = self.cases[self.next % len(self.cases)]
            self.next += 1
        return case

    def run(self, question, emit, cancelled, run_id):
        final = None
        for e in self.pick(question)[1:]:     # the service publishes its own question/status events
            delay = (e.get('seconds') or 0) * self.speed
            interruptible_sleep(min(delay, self.max_delay) if self.max_delay is not None else delay, cancelled)
            emit(e)
            if e['event'] == 'final':
                final = e['response']
            if e['event'] == 'error':
                raise RuntimeError(e['message'])
        return {'final': final}


class GraphAgent:
    def __init__(self, config_path, url='http://127.0.0.1:8100', workdir=Path('data/service/runs')):
        from finmhqa.agent.evidence_context import EvidenceContext
        from finmhqa.agent.openai_compat import OpenAICompatTeacher
        from finmhqa.agent.pilot_tools import request_json
        self.config = json.loads(Path(config_path).read_text())
        spec = self.config['teacher']
        self.teacher = OpenAICompatTeacher(spec['base_url'], spec['model'], repair=bool(self.config.get('format_repair')))
        self.url, self.workdir = url, Path(workdir)
        self.dataset = request_json(url + '/health')['admission']['dataset_sha256']
        idx = self.config.get('context_index')
        self.context = EvidenceContext(idx, self.dataset, self.config.get('context_version', 2)) if idx else None
        self.budgets = dict(self.config['budgets'], reserve_calculation=self.config.get('reserve_calculation', False),
                            calculator=self.config.get('calculator', 'v1'),
                            **{k: self.config[k] for k in ('verifier', 'planner', 'context_policy') if k in self.config})

    def run(self, question, emit, cancelled, run_id):
        from finmhqa.agent.graph import AgentGraph
        from finmhqa.agent.pilot_tools import search
        from finmhqa.harness.provenance import retrieval_lock
        folder = self.workdir / run_id
        folder.mkdir(parents=True, exist_ok=False)

        def model(q, history, step, prompt, schema):
            if cancelled():
                raise InterruptedError('cancelled before model call')
            return self.teacher.decide(q, history, folder / f'step_{step:02}', system=prompt, output_schema=schema)

        def locked_search(args):
            with retrieval_lock():
                return search(self.url, args, self.dataset)
        state = AgentGraph(model, locked_search, emit, self.config['arm'], synthesis=self.config['synthesis'],
                           context_reader=self.context.read if self.context else None,
                           reserve_verifier=self.config['reserve_verifier'], **self.budgets).run(question)
        return {'final': state.get('final')}
