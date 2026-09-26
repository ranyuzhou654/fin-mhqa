"""Agent adapters for the service.

GraphAgent runs the real LangGraph agent from a frozen config (needs the retrieval server and model quota).
CassetteAgent replays recorded trajectories with their recorded timings (optionally sped up); it exercises the
whole service path (queue, workers, events, SSE, cancellation) without model calls, for tests and load tests.
"""
import json
from pathlib import Path
import threading
import time

TYPE_TAGS = {'T1': '单跳', 'T2': '两公司运算', 'T3': '条件桥接', 'T4': '比率与增长', 'T5': '三跳', 'T6': '无法回答'}
DEFAULT_EXAMPLES = [
    {'tag': '单跳', 'question': '在泸州老窖2025年半年度报告中，泸州老窖2024年上半年（上年同期）的合并口径归属于母公司股东的净利润是多少？金额请以亿元表示，四舍五入保留两位小数。'},
    {'tag': '条件桥接', 'question': '根据半年报，比较比亚迪与宁德时代2025年上半年合并口径营业收入，选择较高的一家公司，再查询该公司2025年上半年合并口径研发费用是多少？金额以亿元表示，四舍五入保留两位小数。'},
    {'tag': '比率', 'question': '根据半年报，贵州茅台2025年上半年合并口径的归母净利率是多少？（归母净利率=归属于母公司股东的净利润÷营业收入）结果以百分比表示，四舍五入保留两位小数。'},
    {'tag': '无法回答', 'question': '根据半年报，山西汾酒2025年上半年合并口径营业收入是多少？金额以亿元表示，四舍五入保留两位小数。'},
]


def interruptible_sleep(seconds, cancelled, step=0.05):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cancelled():
            return
        time.sleep(min(step, max(0, end - time.monotonic())))


class CassetteAgent:
    def __init__(self, cases, speed=1.0, max_delay=None):
        """cases: folders with trajectory.jsonl (+ result.json). speed: 0.1 = ten times faster than recorded."""
        self.cases, self.traces, self.meta = [], {}, []
        for folder in cases:
            events = [json.loads(l) for l in (Path(folder) / 'trajectory.jsonl').read_text().splitlines()]
            self.cases.append(events)
            meta = Path(folder) / 'meta.json'
            if meta.exists():
                self.meta.append({**json.loads(meta.read_text()), 'question': events[0]['question'],
                                  'traced': (Path(folder) / 'retrieval_traces.jsonl').exists()})
            # retrieval traces recomputed offline for recorded searches (scripts/backfill_retrieval_traces.py)
            traces = Path(folder) / 'retrieval_traces.jsonl'
            if traces.exists():
                self.traces[id(events)] = [json.loads(l) for l in traces.read_text().splitlines()]
        if not self.cases:
            raise ValueError('no cassettes')
        self.by_question = {c[0]['question']: c for c in self.cases}
        self.speed, self.max_delay = speed, max_delay
        self.lock = threading.Lock()
        self.next = 0

    def examples(self, per_type=1):
        """Recorded questions by type, preferring ones whose verifier caught something (more instructive to watch)."""
        if not self.meta:
            return [e for e in DEFAULT_EXAMPLES if e['question'] in self.by_question] or \
                   [{'tag': '录制', 'question': q} for q in list(self.by_question)[:4]]
        out = []
        for t, tag in TYPE_TAGS.items():
            pool = sorted((m for m in self.meta if m.get('type') == t), key=lambda m: (-m.get('verifier_rejections', 0), m['id']))
            out += [{'tag': tag, 'question': m['question'], 'note': '核验曾拒绝并纠正' if m.get('verifier_rejections') else ''}
                    for m in pool[:per_type]]
        return out

    def pick(self, question):
        if question in self.by_question:
            return self.by_question[question]
        with self.lock:
            case = self.cases[self.next % len(self.cases)]
            self.next += 1
        return case

    def run(self, question, emit, cancelled, run_id):
        final, searches = None, 0
        case = self.pick(question)
        traces = self.traces.get(id(case), [])
        for e in case[1:]:     # the service publishes its own question/status events
            delay = (e.get('seconds') or 0) * self.speed
            interruptible_sleep(min(delay, self.max_delay) if self.max_delay is not None else delay, cancelled)
            emit(e)
            if e['event'] == 'tool_request' and e['tool'] == 'search':
                if searches < len(traces) and traces[searches]['args'] == e['args']:
                    t = traces[searches]
                    emit({'event': 'retrieval_trace', 'args': t['args'], 'trace': t['trace'], 'recomputed': True})
                searches += 1
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

    def examples(self):
        return DEFAULT_EXAMPLES

    def traced_search(self, args, emit, request=None):
        """The agent's search plus the diagnostic trace. The trace goes to the event stream only; the agent receives
        exactly the untraced response (identical results, see tests/test_retrieval.py)."""
        from finmhqa.agent.pilot_tools import request_json, search
        from finmhqa.harness.provenance import retrieval_lock
        if set(args) != {'query', 'company', 'mode'}:
            return search(self.url, args, self.dataset)   # let the tool's own validation reject it
        with retrieval_lock():
            body = (request or request_json)(self.url + '/search', dict(args, top_k=5, n_cand=30, expand_parent=True, trace=True))
        if body['dataset_sha256'] != self.dataset or body['index_admission'] != 'ACCEPTED':
            raise ValueError('retrieval dataset/admission changed')
        emit({'event': 'retrieval_trace', 'args': args, 'trace': body.pop('trace')})
        return body

    def run(self, question, emit, cancelled, run_id):
        from finmhqa.agent.graph import AgentGraph
        folder = self.workdir / run_id
        folder.mkdir(parents=True, exist_ok=False)

        def model(q, history, step, prompt, schema):
            if cancelled():
                raise InterruptedError('cancelled before model call')
            return self.teacher.decide(q, history, folder / f'step_{step:02}', system=prompt, output_schema=schema)

        def locked_search(args):
            return self.traced_search(args, emit)
        state = AgentGraph(model, locked_search, emit, self.config['arm'], synthesis=self.config['synthesis'],
                           context_reader=self.context.read if self.context else None,
                           reserve_verifier=self.config['reserve_verifier'], **self.budgets).run(question)
        return {'final': state.get('final')}
