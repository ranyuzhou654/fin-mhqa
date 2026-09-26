"""Record/replay regression: shipped examples replay identically, and real regressions are caught."""
import json
from pathlib import Path
import pytest
from finmhqa.agent import graph as graph_module
from finmhqa.agent.graph import AgentGraph
from finmhqa.harness.replay import replay_case

EXAMPLES = sorted(p.parent for p in Path('docs/examples').glob('*/trajectory.jsonl'))


def manifest(case):
    return json.loads((case / 'manifest.json').read_text())


@pytest.mark.parametrize('case', EXAMPLES, ids=lambda p: p.name)
def test_examples_replay_identically_with_byte_identical_model_inputs(case):
    r = replay_case(case, manifest(case))
    assert r['status'] == 'IDENTICAL', r
    assert r['model_calls'] == sum(1 for p in case.glob('step_*/input.txt'))


def test_changed_budget_logic_is_caught():
    case = Path('docs/examples/f2_column_swap_caught')

    def tighter(*a, **k):
        return AgentGraph(*a, **{**k, 'max_replans': 0})   # a regression: no replanning after the verifier rejects
    r = replay_case(case, manifest(case), graph_factory=tighter)
    assert r['status'] == 'DIVERGED'


def test_changed_prompt_is_caught_before_reusing_a_recorded_reply(monkeypatch):
    case = Path('docs/examples/f2_column_swap_caught')
    monkeypatch.setattr(graph_module, 'VERIFIER_V2', graph_module.VERIFIER_V2 + '补充一句。')
    r = replay_case(case, manifest(case))
    assert r['status'] == 'DIVERGED' and 'model input differs' in r['reason']
    assert r['step'] == 4   # the first verifier call; planner steps before it are unaffected


def test_changed_tool_arguments_are_caught(tmp_path):
    """If the agent would now send different search arguments, no recorded response exists for them."""
    case = Path('docs/examples/clean_single_hop')
    for p in case.rglob('*'):
        if p.is_file():
            (tmp_path / p.relative_to(case)).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / p.relative_to(case)).write_bytes(p.read_bytes())
    lines = (tmp_path / 'trajectory.jsonl').read_text().splitlines()
    req = next(i for i, l in enumerate(lines) if '"event": "tool_request"' in l and '"tool": "search"' in l)
    lines[req] = lines[req].replace('"mode": "hybrid"', '"mode": "dense"')   # file the response under other arguments
    (tmp_path / 'trajectory.jsonl').write_text('\n'.join(lines) + '\n')
    r = replay_case(tmp_path, manifest(case))
    assert r['status'] == 'DIVERGED' and 'unrecorded search call' in r['reason']
