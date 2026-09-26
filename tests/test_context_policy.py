"""Context policy compact_v1: lossless, prefix-stable, identical to the offline estimate, and wired into the graph."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import pytest
from finmhqa.agent.context_policy import compact_history
from finmhqa.agent.graph import AgentGraph

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import context_budget_analysis as offline  # noqa: E402

INPUTS = sorted(ROOT.glob('docs/examples/*/step_*/input.txt'))


def history_of(path):
    text = path.read_text()
    h = json.loads(text[text.index('{"question"'):])['history']
    return h['history'] if isinstance(h, dict) else h


def chunks(items):
    return [c for x in items if isinstance(x, dict) for o in x.get('observations') or [] for c in o['response'].get('results', [])]


@pytest.mark.parametrize('path', INPUTS, ids=lambda p: f'{p.parents[1].name}/{p.parent.name}')
def test_lossless_prefix_stable_and_same_as_offline_estimate(path):
    items = history_of(path)
    compact = compact_history(items)
    full = {c['chunk_id']: c for c in chunks(items)}
    shown = {c['chunk_id']: c for c in chunks(compact) if 'text' in c}
    for c in chunks(compact):   # every observed chunk stays readable: in full, or via a reference to a full one
        assert 'text' in c or c.get('repeat') and c['chunk_id'] in shown or c.get('same_as') in shown
        if 'same_as' in c:      # the twin really carries every number of the folded kv chunk
            kv, md = full[c['chunk_id']]['text'].split('\n', 2)[-1], shown[c['same_as']]['text']
            assert all(n in md for n in offline_numbers(kv))
    for k in range(len(items) + 1):   # appending history never rewrites earlier entries
        assert compact_history(items[:k]) == compact[:k]
    assert compact == offline.transform(items, 'S4', set())
    assert items == history_of(path)   # input not mutated


def offline_numbers(text):
    import re
    return [t for t in re.findall(r'\(?-?\d[\d,]*\.?\d*\)?%?(?!\d)(?!列)', text) if len(t.strip('()%-,')) >= 2]


def test_examples_shrink_by_about_half():
    before = sum(len(json.dumps(history_of(p), ensure_ascii=False)) for p in INPUTS)
    after = sum(len(json.dumps(compact_history(history_of(p)), ensure_ascii=False)) for p in INPUTS)
    assert 0.35 < 1 - after / before < 0.75


MD = {'chunk_id': 'md1', 'company': '甲', 'chunk_type': 'table_md', 'parent_table_id': 't', 'part': 0, 'page_start': 3,
      'text': '【甲】\n表：利润表　单位：元\n| 营业收入 | 100 |', 'source_sha256': 'x' * 64, 'score': 0.9}
KV = {**MD, 'chunk_id': 'kv1', 'chunk_type': 'table_kv', 'text': '【甲】\n表：利润表　单位：元\n营业收入：本期 100'}


def test_graph_shows_compacted_history_but_keeps_full_evidence_for_guards():
    from test_agent_graph import Scripted, calc, finish, query, F, PASS
    final = {**F, 'citations': ['kv1']}   # citing the folded kv chunk stays valid
    m = Scripted([query(), query('营业收入 附注'), calc(), finish(final), PASS])
    events = []
    g = AgentGraph(m, lambda _: {'results': [deepcopy(MD), deepcopy(KV)]}, events.append, synthesis='verified', context_policy='compact_v1')
    state = g.run('甲收入')
    assert state['terminal_reason'] == 'ANSWERED' and state['final']['citations'] == ['kv1']
    first = [c for x in m.inputs[1] if isinstance(x, dict) for o in x.get('observations', []) for c in o['response']['results']]
    assert first[0] == {k: MD[k] for k in ('chunk_id', 'company', 'chunk_type', 'page_start', 'text')}
    assert first[1]['same_as'] == 'md1' and 'text' not in first[1]
    second = [c for x in m.inputs[2] if isinstance(x, dict) for o in x.get('observations', []) for c in o['response']['results']]
    # the second search repeats both: md becomes a repeat reference, the kv (never shown in full) folds again
    assert second[2] == {'chunk_id': 'md1', 'repeat': '同一证据块已在前文给出'} and second[3]['same_as'] == 'md1'
    responses = [e['response'] for e in events if e['event'] == 'tool_response' and e['tool'] == 'search']
    assert responses[0]['results'][1]['text'] == KV['text']   # the trajectory keeps full tool responses
    verifier_input = m.inputs[4]['history']
    assert any('same_as' in c for x in verifier_input for o in x.get('observations', []) for c in o['response']['results'])
    with pytest.raises(ValueError, match='context policy'):
        AgentGraph(m, None, print, context_policy='summary')
