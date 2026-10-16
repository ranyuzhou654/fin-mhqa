"""Agent adapters for the service.

GraphAgent runs the real LangGraph agent from a frozen config (needs the retrieval server and model quota). In a
conversation it turns the latest message into the question the agent answers (prepare_turn: preferences, then the
session's context strategy), and can consult the shared document-structure memory when enabled.
CassetteAgent replays recorded trajectories with their recorded timings (optionally sped up); it exercises the
whole service path (queue, workers, events, SSE, cancellation) without model calls, for tests and load tests.
"""
import json
from pathlib import Path
import re
import threading
import time

TYPE_TAGS = {'T1': '单跳', 'T2': '两公司运算', 'T3': '条件桥接', 'T4': '比率与增长', 'T5': '三跳', 'T6': '无法回答'}
DEFAULT_EXAMPLES = [
    {'tag': '单跳', 'question': '在泸州老窖2025年半年度报告中，泸州老窖2024年上半年（上年同期）的合并口径归属于母公司股东的净利润是多少？金额请以亿元表示，四舍五入保留两位小数。'},
    {'tag': '条件桥接', 'question': '根据半年报，比较比亚迪与宁德时代2025年上半年合并口径营业收入，选择较高的一家公司，再查询该公司2025年上半年合并口径研发费用是多少？金额以亿元表示，四舍五入保留两位小数。'},
    {'tag': '比率', 'question': '根据半年报，贵州茅台2025年上半年合并口径的归母净利率是多少？（归母净利率=归属于母公司股东的净利润÷营业收入）结果以百分比表示，四舍五入保留两位小数。'},
    {'tag': '无法回答', 'question': '根据半年报，山西汾酒2025年上半年合并口径营业收入是多少？金额以亿元表示，四舍五入保留两位小数。'},
]


SPECIFIED = re.compile(r'亿元|万元|千元|元表示|以元|百分比|%|％|小数')
MEMORY_NOTE = ('以下证据块由文档结构记忆预取：以往通过核验的答案曾在这些表格中找到本题所提公司的同名指标行。'
               '它们只是线索，不保证期间、口径和单位符合本题，使用前必须自行核对；也可以照常检索。')


def apply_preferences(question, prefs):
    """The user's unit and rounding preferences, added only when the question states neither."""
    if not prefs or SPECIFIED.search(question) or not (prefs.get('unit') or prefs.get('decimals')):
        return question, None
    unit, digits = prefs.get('unit', '亿元'), prefs.get('decimals', '2')
    note = f'（如结果为金额，以{unit}表示；四舍五入保留{digits}位小数）'
    return question + note, note


def prepare_turn(question, context, decide, memory=None, by_id=None, companies=()):
    """What the agent should answer for this message, and which already-observed chunks it starts with.

    decide(payload) -> {'standalone', 'reason'} is the rewrite call (S3/S4). Returns (agent question, seeded chunks,
    note for the seeded chunks, events to publish)."""
    from finmhqa.agent.conversation import CARRY_NOTE, carried_evidence, rewrite_payload, with_transcript
    context = context or {}
    events, seeded, notes = [], {}, []
    asked, pref_note = apply_preferences(question, context.get('preferences'))
    if pref_note:
        events.append({'event': 'preferences_applied', 'added': pref_note})
    previous = [{'utterance': t['utterance'], 'final': t['final'], 'evidence': t.get('evidence') or {}}
                for t in context.get('turns', [])]
    strategy = context.get('strategy', 'S3')
    standalone = asked
    if previous and strategy == 'S2':
        standalone = with_transcript(previous, asked)
        events.append({'event': 'context_strategy', 'strategy': 'S2', 'turns': len(previous)})
    elif previous and strategy in ('S3', 'S4'):
        decision = decide(rewrite_payload(previous, asked))
        standalone = decision['standalone']
        events.append({'event': 'rewrite', 'original': question, 'standalone': standalone, 'reason': decision.get('reason', ''),
                       'strategy': strategy})
        if strategy == 'S4':
            seeded.update(carried_evidence(previous))
            if seeded:
                notes.append(CARRY_NOTE)
                events.append({'event': 'carried_evidence', 'chunk_ids': list(seeded)})
    if memory is not None and by_id is not None:
        hints = [c for c in memory.hints(standalone, companies) if c not in seeded and c in by_id]
        if hints:
            seeded.update({c: by_id[c] for c in hints})
            notes.append(MEMORY_NOTE)
            events.append({'event': 'memory_hints', 'chunk_ids': hints})
    return standalone, seeded, '\n'.join(notes), events


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

    def run(self, question, emit, cancelled, run_id, context=None):
        """Replays a recording; the conversation context is not used (recordings are single questions)."""
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
    def __init__(self, config_path, url='http://127.0.0.1:8100', workdir=Path('data/service/runs'), doc_memory=None):
        """doc_memory: path of the shared document-structure memory (off unless given; see MEMORY_V1_PLAN)."""
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
        self.memory = None
        if doc_memory:
            from finmhqa.memory.doc_memory import DocMemory
            self.memory = DocMemory(str(doc_memory))
        self.companies = sorted({c['company'] for c in self.context.by_id.values()}) if self.context else []

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

    def run(self, question, emit, cancelled, run_id, context=None):
        from finmhqa.agent.conversation import REWRITE_SCHEMA, REWRITER, CarryAgentGraph
        folder = self.workdir / run_id
        folder.mkdir(parents=True, exist_ok=False)

        def rewrite(payload):
            decision, usage = self.teacher.decide(question, payload, folder / 'rewrite', system=REWRITER, output_schema=REWRITE_SCHEMA)
            emit({'event': 'teacher_decision', 'role': 'rewriter', 'usage': usage})
            return decision
        standalone, seeded, note, events = prepare_turn(question, context, rewrite, self.memory,
                                                        self.context.by_id if self.context else None, self.companies)
        for e in events:
            emit(e)

        def model(q, history, step, prompt, schema):
            if cancelled():
                raise InterruptedError('cancelled before model call')
            return self.teacher.decide(q, history, folder / f'step_{step:02}', system=prompt, output_schema=schema)

        def locked_search(args):
            return self.traced_search(args, emit)
        state = CarryAgentGraph(model, locked_search, emit, self.config['arm'], synthesis=self.config['synthesis'],
                                context_reader=self.context.read if self.context else None,
                                reserve_verifier=self.config['reserve_verifier'], **self.budgets
                                ).run(standalone, seeded or None, note=note, source='seeded_evidence')
        final = state.get('final')
        if self.memory is not None and final and final.get('status') == 'answered':
            emit({'event': 'memory_write', **self.memory.learn(final, run_id, self.context.by_id)})
        cited = {c: state['evidence'][c] for c in (final or {}).get('citations', []) if c in state.get('evidence', {})}
        return {'final': final, 'standalone': standalone if standalone != question else None, 'evidence': cited}
