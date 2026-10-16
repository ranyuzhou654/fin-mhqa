"""Dialogue strategies around the frozen graph: transcript text, carried evidence, and a seeded graph run."""
from copy import deepcopy
from finmhqa.agent.graph import AgentGraph
from finmhqa.agent.conversation import CarryAgentGraph, answer_text, carried_evidence, transcript, with_transcript
from test_agent_graph import C, F, PASS, Scripted, calc, finish, query

OTHER = {'chunk_id': 'c2', 'company': '乙', 'text': '乙公司研发费用', 'report_period': '2025H1'}


def test_transcript_uses_the_systems_own_answers():
    prev = [{'utterance': '甲收入？', 'final': F}, {'utterance': '乙呢？', 'final': {'status': 'insufficient'}},
            {'utterance': '丙呢？', 'final': None}]
    text = transcript(prev)
    assert '第1轮 系统答案：100.00 元' in text and '证据不足' in text and '出错' in text
    assert with_transcript(prev, '两者相差多少？').endswith('当前问题：两者相差多少？')
    assert '选中的公司：乙' in answer_text({**F, 'selected_company': '乙'})


def test_only_cited_evidence_of_published_answers_is_carried():
    prev = [{'final': F, 'evidence': {'c1': C, 'c9': OTHER}},                       # c9 seen but not cited
            {'final': {'status': 'insufficient', 'citations': ['c2']}, 'evidence': {'c2': OTHER}},
            {'final': {**F, 'citations': ['c1']}, 'evidence': {'c1': C}}]           # duplicate citation
    assert list(carried_evidence(prev)) == ['c1']


def test_carried_evidence_can_be_cited_without_a_new_search():
    m, events = Scripted([calc(), finish(), PASS]), []
    g = CarryAgentGraph(m, lambda _: (_ for _ in ()).throw(AssertionError('no search expected')), events.append,
                        'structured', synthesis='verified')
    state = g.run('两者之和？', carried={'c1': deepcopy(C)})
    assert state['terminal_reason'] == 'ANSWERED' and state['final']['citations'] == ['c1']
    seeded = m.inputs[0][0]
    assert seeded['action']['action'] == 'carried_evidence' and seeded['observations'][0]['response']['results'][0]['chunk_id'] == 'c1'
    assert any(e['event'] == 'carried_evidence' for e in events)


def test_without_carry_the_run_is_the_frozen_graph():
    m, events = Scripted([query(), calc(), finish(), PASS]), []
    state = CarryAgentGraph(m, lambda _: {'results': [C]}, events.append, 'structured', synthesis='verified').run('甲收入多少')
    assert state['final'] == F and not any(e['event'] == 'carried_evidence' for e in events)
    plain = Scripted([query(), calc(), finish(), PASS])
    AgentGraph(plain, lambda _: {'results': [C]}, [].append, 'structured', synthesis='verified').run('甲收入多少')
    assert m.inputs == plain.inputs                     # model inputs byte-for-byte as the frozen graph's
