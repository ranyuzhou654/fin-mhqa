"""Agent service: API, SSE, isolation, cancellation, timeouts, limits, fairness and restart (no model calls)."""
import json
from pathlib import Path
import threading
import time
import pytest
from fastapi.testclient import TestClient
from finmhqa.service.agents import CassetteAgent
from finmhqa.service.app import create_app
from finmhqa.service.observability import Metrics
from finmhqa.service.scheduler import Scheduler
from finmhqa.service.store import TERMINAL, Store

EXAMPLE = Path('docs/examples/f2_column_swap_caught')


def cassette(tmp_path, name, question, delays):
    """A recorded-looking trajectory whose model calls take the given seconds."""
    folder = tmp_path / name
    folder.mkdir()
    final = {'status': 'answered', 'answer_value': '1.00', 'answer_unit': '亿元', 'selected_company': None, 'citations': ['c1'],
             'explanation': name}
    events = [{'event': 'question', 'id': name, 'question': question, 'arm': 'structured'}]
    for i, d in enumerate(delays):
        events.append({'event': 'teacher_decision', 'step': i, 'role': 'planner', 'seconds': d, 'usage': {'input_tokens': 1000, 'output_tokens': 10},
                       'action': {'action': 'search', 'reason': f'step {i}'}})
    events.append({'event': 'final', 'response': final, 'terminal_reason': 'ANSWERED'})
    (folder / 'trajectory.jsonl').write_text(''.join(json.dumps(e, ensure_ascii=False) + '\n' for e in events))
    return folder


def service(agent, workers=2, max_queue=100, run_timeout=60, heartbeat=15, store=None):
    store = store or Store()
    metrics = Metrics()
    scheduler = Scheduler(store, agent, metrics=metrics, workers=workers, max_queue=max_queue, run_timeout=run_timeout)
    return create_app(store, scheduler, metrics, heartbeat=heartbeat), store, scheduler


def auth(key):
    return {'Authorization': f'Bearer {key}'}


def read_sse(client, rid, key, last=None, stop_after=None, detail='summary'):
    headers = {**auth(key), **({'Last-Event-ID': str(last)} if last else {})}
    out = []
    with client.stream('GET', f'/runs/{rid}/events?detail={detail}', headers=headers) as r:
        assert r.status_code == 200 and r.headers['content-type'].startswith('text/event-stream')
        block = {}
        for line in r.iter_lines():
            if line.startswith(': '):
                out.append({'comment': line[2:]})
            elif line.startswith('id: '):
                block['id'] = int(line[4:])
            elif line.startswith('event: '):
                block['event'] = line[7:]
            elif line.startswith('data: '):
                block['data'] = json.loads(line[6:])
            elif line == '' and 'event' in block:
                out.append(block)
                if block['event'] == 'end' or (stop_after and len([b for b in out if 'id' in b]) >= stop_after):
                    break
                block = {}
    return out


def ask(client, key, question='测试问题：某公司营业收入是多少？'):
    sid = client.post('/sessions', json={}, headers=auth(key)).json()['id']
    r = client.post(f'/sessions/{sid}/questions', json={'question': question}, headers=auth(key))
    return sid, r


def test_full_flow_streams_ordered_events_and_final_answer():
    app, store, _ = service(CassetteAgent([EXAMPLE], speed=0))
    _, key = store.create_user('alice')
    with TestClient(app) as c:
        question = json.loads((EXAMPLE / 'trajectory.jsonl').read_text().splitlines()[0])['question']
        sid, r = ask(c, key, question)
        assert r.status_code == 202 and r.headers['location'] == f"/runs/{r.json()['run_id']}"
        rid = r.json()['run_id']
        events = [e for e in read_sse(c, rid, key) if 'id' in e]
        assert [e['id'] for e in events] == list(range(1, len(events) + 1))
        assert events[0]['event'] == 'status' and events[-1]['event'] == 'end' and events[-1]['data']['status'] == 'succeeded'
        assert events[-1]['data']['final']['answer_value'] == '80.28'
        kinds = {e['event'] for e in events}
        assert {'teacher_decision', 'tool_request', 'tool_response', 'verification', 'final', 'retrieval_trace'} <= kinds
        trace = next(e for e in events if e['event'] == 'retrieval_trace')
        request = max(e['id'] for e in events if e['event'] == 'tool_request' and e['id'] < trace['id'])
        response = min(e['id'] for e in events if e['event'] == 'tool_response' and e['id'] > trace['id'])
        assert request < trace['id'] < response and trace['data']['recomputed'] is True   # sits between its search request and response
        assert trace['data']['trace']['summary']['overlap'] == 13 and len(trace['data']['trace']['dense']) == 30
        summary = next(e for e in events if e['event'] == 'tool_response' and 'results' in e['data']['response'])
        assert all('text' not in ch for ch in summary['data']['response']['results'])   # summary detail: labels only
        run = c.get(f'/runs/{rid}', headers=auth(key)).json()
        assert run['status'] == 'succeeded' and run['final']['answer_value'] == '80.28' and run['input_tokens'] > 0
        full = c.get(f'/runs/{rid}/trajectory', headers=auth(key)).json()['events']
        assert any('text' in ch for e in full for ch in (e['data'].get('response') or {}).get('results', []))
        assert c.get(f'/sessions/{sid}', headers=auth(key)).json()['runs'][0]['id'] == rid


def test_auth_and_isolation_between_users():
    app, store, _ = service(CassetteAgent([EXAMPLE], speed=0))
    _, alice = store.create_user('alice')
    _, bob = store.create_user('bob')
    _, admin = store.create_user('ops', is_admin=True)
    with TestClient(app) as c:
        assert c.post('/sessions', json={}).status_code == 401
        assert c.post('/sessions', json={}, headers=auth('fmq_wrong')).status_code == 401
        sid, r = ask(c, alice)
        rid = r.json()['run_id']
        read_sse(c, rid, alice)
        for method, url in [('GET', f'/sessions/{sid}'), ('GET', f'/runs/{rid}'), ('GET', f'/runs/{rid}/events'),
                            ('GET', f'/runs/{rid}/trajectory'), ('DELETE', f'/runs/{rid}')]:
            assert c.request(method, url, headers=auth(bob)).status_code == 404, url   # not 403: existence not revealed
        assert c.post(f'/sessions/{sid}/questions', json={'question': '别人的会话能提问吗？'}, headers=auth(bob)).status_code == 404
        assert c.get('/metrics', headers=auth(alice)).status_code == 404
        m = c.get('/metrics', headers=auth(admin))
        assert m.status_code == 200 and 'finmhqa_runs_finished_total{status="succeeded"} 1' in m.text


def test_reconnect_with_last_event_id_neither_loses_nor_repeats(tmp_path):
    app, store, _ = service(CassetteAgent([cassette(tmp_path, 'slow', 'q', [0.2] * 6)]))
    _, key = store.create_user('alice')
    with TestClient(app) as c:
        _, r = ask(c, key)
        rid = r.json()['run_id']
        first = [e for e in read_sse(c, rid, key, stop_after=3) if 'id' in e]
        rest = [e for e in read_sse(c, rid, key, last=first[-1]['id']) if 'id' in e]
        ids = [e['id'] for e in first + rest]
        assert ids == list(range(1, len(ids) + 1)) and rest[-1]['event'] == 'end'


def test_cancel_ends_stream_within_two_seconds_and_is_not_overwritten(tmp_path):
    app, store, _ = service(CassetteAgent([cassette(tmp_path, 'long', 'q', [1.0] * 30)]))
    _, key = store.create_user('alice')
    with TestClient(app) as c:
        _, r = ask(c, key)
        rid = r.json()['run_id']
        time.sleep(0.3)
        t0 = time.monotonic()
        assert c.delete(f'/runs/{rid}', headers=auth(key)).json()['cancelled'] is True
        end = read_sse(c, rid, key)[-1]
        assert end['event'] == 'end' and end['data']['status'] == 'cancelled' and time.monotonic() - t0 < 2
        time.sleep(1.2)   # the worker reaches its next event and stops; a terminal status is never overwritten
        assert c.get(f'/runs/{rid}', headers=auth(key)).json()['status'] == 'cancelled'
        assert c.delete(f'/runs/{rid}', headers=auth(key)).json()['cancelled'] is False


def test_timeout_and_heartbeat(tmp_path):
    app, store, _ = service(CassetteAgent([cassette(tmp_path, 'stuck', 'q', [5.0])]), run_timeout=0.8, heartbeat=0.3)
    _, key = store.create_user('alice')
    with TestClient(app) as c:
        _, r = ask(c, key)
        events = read_sse(c, r.json()['run_id'], key)
        assert any(e.get('comment') == 'ping' for e in events)
        assert events[-1]['data']['status'] == 'timed_out'


def test_input_validation_and_user_limits(tmp_path):
    app, store, _ = service(CassetteAgent([cassette(tmp_path, 'quick', 'q', [0.0])]))
    _, key = store.create_user('alice', rate_per_min=2, max_queued=10, daily_tokens=10**9)
    _, small = store.create_user('small', rate_per_min=100, max_queued=10, daily_tokens=500)
    with TestClient(app) as c:
        sid = c.post('/sessions', json={}, headers=auth(key)).json()['id']
        assert c.post(f'/sessions/{sid}/questions', json={'question': 'ab'}, headers=auth(key)).status_code == 422
        assert c.post(f'/sessions/{sid}/questions', json={'question': 'x' * 501}, headers=auth(key)).status_code == 422
        assert c.post(f'/sessions/{sid}/questions', json={'question': '\x00\x01\x02\x03\x04'}, headers=auth(key)).status_code == 422
        codes = [ask(c, key, f'问题 {i} 是什么？')[1] for i in range(3)]   # rate limits are per user, across conversations
        assert [r.status_code for r in codes] == [202, 202, 429] and int(codes[2].headers['retry-after']) >= 1
        _, r = ask(c, small)
        read_sse(c, r.json()['run_id'], small)            # the first run uses 1,010 tokens, over the 500 quota
        _, again = ask(c, small)
        assert again.status_code == 429 and 'daily token quota' in again.json()['detail']


def test_queue_full_returns_503(tmp_path):
    app, store, _ = service(CassetteAgent([cassette(tmp_path, 'long', 'q', [2.0] * 5)]), workers=1, max_queue=1)
    _, key = store.create_user('alice', max_concurrent=1, max_queued=10)
    with TestClient(app) as c:
        codes = []
        for i in range(3):   # separate conversations: one conversation takes one turn at a time (409)
            sid = c.post('/sessions', json={}, headers=auth(key)).json()['id']
            codes.append(c.post(f'/sessions/{sid}/questions', json={'question': f'问题 {i} 是什么？'}, headers=auth(key)).status_code)
            time.sleep(0.2)
        assert codes == [202, 202, 503]   # one executing, one queued, then the queue is full


class Recorder:
    """Agent that logs start order and waits to be released."""

    def __init__(self):
        self.order, self.release = [], threading.Event()

    def run(self, question, emit, cancelled, run_id):
        self.order.append(question)
        self.release.wait(5)
        return {'final': None}


def test_fair_scheduling_between_users():
    store = Store()
    heavy, _ = store.create_user('heavy', max_concurrent=1)
    light, _ = store.create_user('light', max_concurrent=1)
    agent = Recorder()
    s = Scheduler(store, agent, workers=2)
    sid_h, sid_l = store.create_session(heavy)['id'], store.create_session(light)['id']
    for i in range(5):
        s.submit(store.create_run(sid_h, heavy, f'h{i}'), heavy, f'h{i}')
    s.submit(store.create_run(sid_l, light, 'l0'), light, 'l0')
    s.start()
    time.sleep(0.5)
    # two workers, but heavy may only run one at a time: the second worker serves the light user immediately
    assert sorted(agent.order) == ['h0', 'l0']
    agent.release.set()
    s.stop()


def test_restart_marks_unfinished_runs_failed(tmp_path):
    db = tmp_path / 'service.db'
    store = Store(db)
    uid, key = store.create_user('alice')
    sid = store.create_session(uid)['id']
    rid = store.create_run(sid, uid, 'q')
    store.set_status(rid, 'running')
    app, store2, _ = service(CassetteAgent([EXAMPLE], speed=0), store=Store(db))
    with TestClient(app) as c:
        run = c.get(f'/runs/{rid}', headers=auth(key)).json()
        assert run['status'] == 'failed' and 'restarted' in run['error']
        assert read_sse(c, rid, key)[-1]['data']['status'] == 'failed'


def test_api_keys_are_stored_hashed(tmp_path):
    store = Store(tmp_path / 's.db')
    _, key = store.create_user('alice')
    assert key.startswith('fmq_') and key.encode() not in (tmp_path / 's.db').read_bytes()


def test_history_lists_only_own_runs_newest_first():
    app, store, _ = service(CassetteAgent([EXAMPLE], speed=0))
    _, alice = store.create_user('alice')
    _, bob = store.create_user('bob')
    with TestClient(app) as c:
        ids = []
        for q in ('第一个问题是什么？', '第二个问题是什么？'):
            _, r = ask(c, alice, q)
            ids.append(r.json()['run_id'])
            read_sse(c, ids[-1], alice)
        ask(c, bob, 'bob 的问题是什么？')
        runs = c.get('/runs', headers=auth(alice)).json()['runs']
        assert [r['id'] for r in runs] == ids[::-1] and runs[0]['question'] == '第二个问题是什么？'
        assert runs[0]['status'] == 'succeeded' and runs[0]['final']['answer_value'] == '80.28'
        assert len(c.get('/runs?limit=1', headers=auth(alice)).json()['runs']) == 1


def test_graph_agent_sends_trace_to_events_but_not_to_the_agent():
    from finmhqa.service.agents import GraphAgent
    agent = GraphAgent.__new__(GraphAgent)
    agent.url, agent.dataset = 'http://retrieval', 'sha'
    sent, events = [], []

    def fake_request(url, payload):
        sent.append(payload)
        return {'results': [{'chunk_id': 'c1'}], 'dataset_sha256': 'sha', 'index_admission': 'ACCEPTED', 'trace': {'summary': {}}}
    args = {'query': 'q', 'company': '甲', 'mode': 'hybrid'}
    body = agent.traced_search(args, events.append, request=fake_request)
    assert 'trace' not in body and body['results'] == [{'chunk_id': 'c1'}]
    assert sent == [{**args, 'top_k': 5, 'n_cand': 30, 'expand_parent': True, 'trace': True}]
    assert events == [{'event': 'retrieval_trace', 'args': args, 'trace': {'summary': {}}}]
    agent.dataset = 'other'
    with pytest.raises(ValueError, match='dataset'):
        agent.traced_search(args, events.append, request=fake_request)


def test_examples_come_from_recorded_questions_by_type(tmp_path):
    cases = []
    for i, (t, rej) in enumerate([('T1', 0), ('T1', 2), ('T3', 0), ('T6', 0)]):
        c = cassette(tmp_path, f'c{i}', f'问题{i}是什么？', [0.0])
        (c / 'meta.json').write_text(json.dumps({'id': f'c{i}', 'type': t, 'verifier_rejections': rej}))
        cases.append(c)
    app, store, _ = service(CassetteAgent(cases, speed=0))
    with TestClient(app) as c:
        ex = c.get('/examples').json()['examples']
        # one per type, the T1 pick is the one whose verifier caught something
        assert [(e['tag'], e['question']) for e in ex] == [('单跳', '问题1是什么？'), ('条件桥接', '问题2是什么？'), ('无法回答', '问题3是什么？')]
        assert ex[0]['note'] == '核验曾拒绝并纠正'


# ---------- conversations, preferences, memory (multi-turn service) ----------
from finmhqa.service.agents import apply_preferences, prepare_turn   # noqa: E402

PREV = [{'utterance': '甲公司2025年上半年营业收入是多少？', 'final': {'status': 'answered', 'answer_value': '1.00', 'answer_unit': '亿元',
                                                         'citations': ['c1']}, 'evidence': {'c1': {'chunk_id': 'c1', 'text': 't'}}}]


def test_preferences_only_fill_in_what_the_question_left_open():
    q, note = apply_preferences('甲公司营业收入是多少？', {'unit': '万元', 'decimals': '1'})
    assert note and q.endswith(note) and '万元' in note and '1位小数' in note
    assert apply_preferences('甲公司营业收入是多少？金额以亿元表示。', {'unit': '万元'}) == ('甲公司营业收入是多少？金额以亿元表示。', None)
    assert apply_preferences('甲公司营业收入是多少？', {}) == ('甲公司营业收入是多少？', None)


@pytest.mark.parametrize('strategy', ['S1', 'S2', 'S3', 'S4'])
def test_prepare_turn_follows_the_session_strategy(strategy):
    calls = []

    def decide(payload):
        calls.append(payload)
        return {'standalone': '乙公司2025年上半年营业收入是多少？', 'reason': '补全指标与期间'}
    q, seeded, note, events = prepare_turn('乙公司呢？', {'turns': PREV, 'strategy': strategy}, decide)
    kinds = [e['event'] for e in events]
    if strategy == 'S1':
        assert q == '乙公司呢？' and not calls and not seeded
    elif strategy == 'S2':
        assert '第1轮 系统答案：1.00 亿元' in q and q.endswith('当前问题：乙公司呢？') and not calls
    else:
        assert q == '乙公司2025年上半年营业收入是多少？' and 'rewrite' in kinds and '1.00 亿元' in calls[0]['对话记录']
        assert (list(seeded) == ['c1']) == (strategy == 'S4') and (('carried_evidence' in kinds) == (strategy == 'S4'))


def test_first_turn_is_not_rewritten():
    q, seeded, _, events = prepare_turn('甲公司营业收入是多少？', {'turns': [], 'strategy': 'S4'}, lambda p: 1 / 0)
    assert q == '甲公司营业收入是多少？' and not seeded and not events


def test_memory_hints_are_seeded_from_the_index_only():
    class Memory:
        def hints(self, question, companies):
            return ['md1', 'gone']
    q, seeded, note, events = prepare_turn('甲公司营业收入是多少？', {}, None, Memory(), {'md1': {'chunk_id': 'md1'}}, ['甲公司'])
    assert list(seeded) == ['md1'] and '文档结构记忆' in note
    assert events[-1] == {'event': 'memory_hints', 'chunk_ids': ['md1'], 'response': {'results': [{'chunk_id': 'md1'}]}}


class ContextRecorder:
    def __init__(self):
        self.contexts = []

    def run(self, question, emit, cancelled, run_id, context=None):
        self.contexts.append(context)
        emit({'event': 'rewrite', 'original': question, 'standalone': question + '（已补全）'})
        return {'final': {'status': 'answered', 'answer_value': str(len(self.contexts)), 'answer_unit': '亿元', 'citations': ['c']},
                'standalone': question + '（已补全）', 'evidence': {'c': {'chunk_id': 'c', 'text': '原文'}}}


def wait_done(c, key, rid):
    for _ in range(100):
        if c.get(f'/runs/{rid}', headers=auth(key)).json()['status'] in TERMINAL:
            return
        time.sleep(0.05)


def test_conversation_turns_reach_the_agent_with_strategy_and_preferences():
    agent = ContextRecorder()
    app, store, _ = service(agent)
    _, key = store.create_user('alice')
    with TestClient(app) as c:
        assert c.put('/me/preferences', json={'unit': '万元', 'decimals': 1}, headers=auth(key)).json() == {'unit': '万元', 'decimals': '1'}
        sid = c.post('/sessions', json={'strategy': 'S4'}, headers=auth(key)).json()['id']
        r1 = c.post(f'/sessions/{sid}/questions', json={'question': '甲公司营业收入是多少？'}, headers=auth(key)).json()['run_id']
        wait_done(c, key, r1)
        assert c.patch(f'/sessions/{sid}', json={'strategy': 'S3'}, headers=auth(key)).json()['strategy'] == 'S3'
        r2 = c.post(f'/sessions/{sid}/questions', json={'question': '乙公司呢？'}, headers=auth(key)).json()['run_id']
        wait_done(c, key, r2)
        first, second = agent.contexts
        assert first['turns'] == [] and first['strategy'] == 'S4' and first['preferences']['unit'] == '万元'
        assert second['strategy'] == 'S3' and second['turns'][0]['utterance'] == '甲公司营业收入是多少？'
        assert second['turns'][0]['evidence'] == {'c': {'chunk_id': 'c', 'text': '原文'}}      # cited text carried internally
        run = c.get(f'/runs/{r1}', headers=auth(key)).json()
        assert run['standalone'] == '甲公司营业收入是多少？（已补全）' and 'evidence' not in run   # not exposed by the API
        listing = c.get('/sessions', headers=auth(key)).json()['sessions']
        assert listing[0]['id'] == sid and listing[0]['turns'] == 2 and listing[0]['first_question'] == '甲公司营业收入是多少？'


def test_one_turn_at_a_time_and_validation():
    app, store, _ = service(Recorder())
    _, key = store.create_user('alice')
    with TestClient(app) as c:
        sid = c.post('/sessions', json={}, headers=auth(key)).json()['id']
        assert c.post(f'/sessions/{sid}/questions', json={'question': '第一个问题是什么？'}, headers=auth(key)).status_code == 202
        busy = c.post(f'/sessions/{sid}/questions', json={'question': '第二个问题是什么？'}, headers=auth(key))
        assert busy.status_code == 409 and '上一轮' in busy.json()['detail']
        assert c.post('/sessions', json={'strategy': 'S9'}, headers=auth(key)).status_code == 422
        assert c.patch(f'/sessions/{sid}', json={'strategy': 'S9'}, headers=auth(key)).status_code == 422
        assert c.put('/me/preferences', json={'unit': '美元'}, headers=auth(key)).status_code == 422
        assert set(c.get('/options').json()['strategies']) == {'S1', 'S2', 'S3', 'S4'}


def test_preferences_and_conversations_are_private():
    app, store, _ = service(ContextRecorder())
    _, ka = store.create_user('alice')
    _, kb = store.create_user('bob')
    with TestClient(app) as c:
        c.put('/me/preferences', json={'watchlist': ['比亚迪']}, headers=auth(ka))
        assert c.get('/me/preferences', headers=auth(kb)).json() == {}
        sid = c.post('/sessions', json={}, headers=auth(ka)).json()['id']
        assert c.get('/sessions', headers=auth(kb)).json()['sessions'] == []
        assert c.patch(f'/sessions/{sid}', json={'strategy': 'S1'}, headers=auth(kb)).status_code == 404
