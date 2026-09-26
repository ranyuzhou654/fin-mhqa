"""Record/replay regression for graph runs: no model calls, no retrieval server.

A recorded case folder (trajectory.jsonl, step_NN/input.txt + schema.json, and the run manifest) is replayed
through the current AgentGraph code:
- model calls return the recorded (validated) decisions, but only after checking that the current code builds a
  byte-identical model input and schema; otherwise the recorded reply would not apply and the case DIVERGES;
- search / context calls return the recorded tool responses, looked up by exact arguments;
- the calculator runs for real;
- the resulting event stream must equal the recorded one (timings ignored).
"""
from copy import deepcopy
import json
from pathlib import Path
from finmhqa.agent.graph import AgentGraph

GRAPH_ARMS = ('structured', 'generic', 'flat')
BUDGET_KEYS = ('max_models', 'max_tools', 'max_searches', 'max_replans', 'reserve_calculation', 'calculator', 'verifier', 'planner')


class Divergence(Exception):
    def __init__(self, step, reason):
        super().__init__(f'step {step}: {reason}')
        self.step, self.reason = step, reason


def graph_kwargs(manifest):
    kw = {k: manifest[k] for k in BUDGET_KEYS if k in manifest}
    return dict(kw, synthesis=manifest['synthesis'], reserve_verifier=manifest['reserve_verifier'])


def strip(event):
    return {k: v for k, v in event.items() if k not in ('seconds', 'error_class')}


def first_difference(a, b):
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return i
    return min(len(a), len(b)) if len(a) != len(b) else None


def replay_case(folder, manifest, check_inputs=True, graph_factory=AgentGraph):
    folder = Path(folder)
    recorded = [json.loads(l) for l in (folder / 'trajectory.jsonl').read_text().splitlines()]
    arm = recorded[0]['arm']
    if arm not in GRAPH_ARMS:
        return {'id': folder.name, 'status': 'SKIPPED', 'reason': f'{arm} is not a graph arm'}
    decisions = [e for e in recorded if e['event'] == 'teacher_decision']
    error = next((e['message'] for e in recorded if e['event'] == 'error'), None)
    # recorded tool responses by (tool, exact args), in call order
    responses, pending = {}, None
    for e in recorded:
        if e['event'] == 'tool_request':
            pending = e
        elif e['event'] == 'tool_response' and e['tool'] in ('search', 'read_evidence_context'):
            key = (e['tool'], json.dumps(pending['args'], sort_keys=True, ensure_ascii=False))
            responses.setdefault(key, []).append(e['response'])
    used = {'decisions': 0}

    def model(question, history, step, prompt, schema):
        i = used['decisions']
        if i >= len(decisions):
            if error:   # the recorded run failed at this model call (e.g. transport or format); fail the same way
                name, _, message = error.partition(': ')
                raise type(name, (Exception,), {})(message)
            raise Divergence(step, 'model called more often than recorded')
        d = decisions[i]
        used['decisions'] += 1
        if d['step'] != step:
            raise Divergence(step, f"recorded decision is step {d['step']}")
        step_dir = folder / f'step_{step:02}'
        if check_inputs and (step_dir / 'input.txt').exists():
            actual = prompt + '\n' + json.dumps({'question': question, 'history': history}, ensure_ascii=False)
            expected = (step_dir / 'input.txt').read_text()
            if actual != expected:
                at = first_difference(actual, expected)
                raise Divergence(step, f'model input differs at char {at}: current {actual[at:at + 60]!r} vs recorded {expected[at:at + 60]!r}')
            if json.loads((step_dir / 'schema.json').read_text()) != schema:
                raise Divergence(step, 'output schema differs')
        return deepcopy(d['action']), d['usage']

    def tool(name):
        def call(args):
            key = (name, json.dumps(args, sort_keys=True, ensure_ascii=False))
            queue = responses.get(key)
            if not queue:
                raise Divergence(used['decisions'] - 1, f'unrecorded {name} call {key[1][:120]}')
            r = deepcopy(queue.pop(0))
            if 'error' in r:   # re-raise with the recorded exception type name so the graph records it identically
                raise type(r['error'], (ValueError,), {})(r.get('detail', ''))
            return r
        return call

    events = [recorded[0]]
    # the runner serializes each event when it is recorded; copy here too, the graph mutates lists it has logged
    graph = graph_factory(model, tool('search'), lambda e: events.append(deepcopy(e)), arm,
                          context_reader=tool('read_evidence_context') if manifest.get('context_index') else None,
                          **graph_kwargs(manifest))
    try:
        graph.run(recorded[0]['question'])
    except Divergence as d:
        return {'id': folder.name, 'status': 'DIVERGED', 'step': d.step, 'reason': d.reason}
    except Exception as exc:
        events.append({'event': 'error', 'message': f'{type(exc).__name__}: {exc}'})
    a, b = [strip(e) for e in events], [strip(e) for e in recorded]
    at = first_difference(a, b)
    if at is not None:
        got = json.dumps(a[at], ensure_ascii=False)[:160] if at < len(a) else 'END'
        want = json.dumps(b[at], ensure_ascii=False)[:160] if at < len(b) else 'END'
        return {'id': folder.name, 'status': 'DIVERGED', 'event': at, 'reason': f'event {at}: replay {got} vs recorded {want}'}
    leftover = sum(1 for q in responses.values() for r in q if 'error' not in r)
    if leftover:
        return {'id': folder.name, 'status': 'DIVERGED', 'reason': f'{leftover} recorded tool responses were never requested'}
    return {'id': folder.name, 'status': 'IDENTICAL', 'events': len(events), 'model_calls': used['decisions']}


def replay_run(run, check_inputs=True):
    manifest = json.loads((Path(run) / 'manifest.json').read_text())
    out = []
    for arm in manifest['arms']:
        for case in sorted(p for p in (Path(run) / arm).iterdir() if (p / 'trajectory.jsonl').exists()):
            out.append(replay_case(case, manifest, check_inputs))
    return out
