"""Run harness: error classes, quota fail-fast, per-item resume, and an end-to-end runner test on fake servers."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import urllib.error
import pytest
from finmhqa.agent.openai_compat import OpenAICompatTeacher
from finmhqa.harness.runs import (QuotaExhausted, classify_error, execute, is_quota_response, item_state, plan_resume,
                                  summarize_run, write_status)

GO_LIMIT = 'HTTP 429: {"type":"error","error":{"type":"GoUsageLimitError","message":"Go usage limit exceeded"},"metadata":{"limitName":"weekly"}}'


@pytest.mark.parametrize('message,expected', [
    (None, None),
    (f'RuntimeError: teacher transport failed: {GO_LIMIT}', 'quota'),   # how the voided planner v2 runs recorded it
    (f'QuotaExhausted: {GO_LIMIT}', 'quota'),
    ('RuntimeError: teacher transport failed: HTTP 402: Insufficient account funds', 'quota'),
    ('RuntimeError: teacher transport failed: Remote end closed connection without response', 'transport'),
    ('IncompleteRead: IncompleteRead(9675 bytes read, 1783 more expected)', 'transport'),
    ('RuntimeError: teacher transport failed: HTTP 429: rate limited, slow down', 'transport'),
    ('ValueError: plan size', 'agent'),
    ("RuntimeError: invalid output on attempt 2: 'reason' is a required property", 'agent'),
])
def test_error_classes_match_recorded_messages(message, expected):
    assert classify_error(message) == expected


def test_quota_responses_are_told_apart_from_rate_limits():
    assert is_quota_response(429, GO_LIMIT[9:]) and is_quota_response(402, '')
    assert not is_quota_response(429, '{"error":"rate limit, retry later"}') and not is_quota_response(503, 'quota')


def test_teacher_raises_quota_without_retry(tmp_path, monkeypatch):
    calls = []

    def urlopen(request, timeout):
        calls.append(1)
        raise urllib.error.HTTPError(request.full_url, 429, 'Too Many Requests', {}, io.BytesIO(GO_LIMIT[9:].encode()))
    monkeypatch.setattr('urllib.request.urlopen', urlopen)
    t = OpenAICompatTeacher('https://example.test/v1', 'm', api_key='k', backoff=0)
    with pytest.raises(QuotaExhausted, match='GoUsageLimitError'):
        t.decide('问', [], tmp_path / 's', system='S', output_schema={'type': 'object'})
    assert len(calls) == 1
    assert json.loads((tmp_path / 's' / 'transport_errors.json').read_text())[0]['retried'] is False


def make_item(root, arm, qid, error=None, result=True):
    folder = root / arm / qid
    folder.mkdir(parents=True)
    (folder / 'trajectory.jsonl').write_text('{}\n')
    if result:
        (folder / 'result.json').write_text(json.dumps({'id': qid, 'error': error}))
    return folder


def test_plan_resume_reruns_only_missing_partial_and_infrastructure_failures(tmp_path):
    make_item(tmp_path, 'flat', 'done')
    make_item(tmp_path, 'flat', 'agent', 'ValueError: plan size')
    make_item(tmp_path, 'flat', 'infra', 'IncompleteRead: cut')
    make_item(tmp_path, 'flat', 'partial', result=False)
    tasks = [('flat', {'id': i}) for i in ('done', 'agent', 'infra', 'partial', 'missing')]
    assert summarize_run(tmp_path, tasks)['status'] == 'INCOMPLETE'
    todo, moved = plan_resume(tmp_path, tasks)
    assert [r['id'] for _, r in todo] == ['infra', 'partial', 'missing']
    assert {m['id']: m['moved_to'] for m in moved} == {'infra': 'superseded/flat/infra__1', 'partial': 'superseded/flat/partial__1'}
    assert (tmp_path / 'superseded/flat/infra__1/result.json').exists() and not (tmp_path / 'flat/infra').exists()
    assert item_state(tmp_path / 'flat/agent') == 'agent_error'   # an agent failure is a result, never rerun
    make_item(tmp_path, 'flat', 'infra', 'IncompleteRead: again')
    plan_resume(tmp_path, tasks)
    assert (tmp_path / 'superseded/flat/infra__2').exists()


def test_execute_stops_unstarted_tasks_after_quota():
    seen = []

    def run_one(task):
        seen.append(task)
        return 'quota' if task == 2 else None
    started, stopped = execute(list(range(10)), run_one, workers=1)
    assert stopped and seen == [0, 1, 2] and started == [0, 1, 2]


def test_status_history_is_kept(tmp_path):
    write_status(tmp_path, {'status': 'INCOMPLETE'}, stopped_by_quota=True)
    write_status(tmp_path, {'status': 'COMPLETE'})
    data = json.loads((tmp_path / 'run_status.json').read_text())
    assert data['status'] == 'COMPLETE' and [h['status'] for h in data['history']] == ['INCOMPLETE', 'COMPLETE']


# ---------- end to end: the real runner against fake retrieval + model servers ----------

ANSWER = {'status': 'insufficient', 'answer_value': '', 'answer_unit': '', 'selected_company': None, 'citations': [],
          'explanation': 'fake closed-book answer'}


class Fake(BaseHTTPRequestHandler):
    quota_after = None   # model calls allowed before the fake provider starts refusing with a weekly limit
    calls = 0

    def log_message(self, *args):
        pass

    def reply(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.reply(200, {'count': 9322, 'admission': {'status': 'ACCEPTED', 'dataset_sha256': 'fake'}})

    def do_POST(self):
        self.rfile.read(int(self.headers['Content-Length']))
        cls = type(self)
        cls.calls += 1
        if cls.quota_after is not None and cls.calls > cls.quota_after:
            return self.reply(429, json.loads(GO_LIMIT[9:]))
        self.reply(200, {'id': 'r', 'choices': [{'message': {'content': json.dumps(ANSWER)}}],
                         'usage': {'prompt_tokens': 10, 'completion_tokens': 5}})


@pytest.fixture
def fake_server():
    server = ThreadingHTTPServer(('127.0.0.1', 0), Fake)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    Fake.calls, Fake.quota_after = 0, None
    yield f'http://127.0.0.1:{server.server_port}'
    server.shutdown()


def runner(tmp_path, url, *extra):
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'teacher': {'backend': 'openai', 'base_url': url + '/v1', 'model': 'fake'}, 'arm': 'closed_book',
                                  'synthesis': 'verified', 'context_index': None, 'reserve_verifier': False,
                                  'budgets': {'max_models': 1, 'max_tools': 1, 'max_searches': 1, 'max_replans': 0}}))
    questions = tmp_path / 'q.jsonl'
    questions.write_text(''.join(json.dumps({'id': f'q{i}', 'question': f'问题{i}'}) + '\n' for i in range(6)))
    env = {**os.environ, 'PYTHONPATH': 'src', 'LLM_API_KEY': 'test-key'}
    return subprocess.run([sys.executable, 'scripts/run_agent_dev.py', '--questions', str(questions), '--config', str(config),
                           '--url', url, '--workers', '1', '--out', str(tmp_path / 'run'), *extra],
                          capture_output=True, text=True, env=env, cwd=Path(__file__).parents[1])


def test_runner_stops_on_quota_then_resumes_only_unfinished_items(tmp_path, fake_server):
    Fake.quota_after = 3
    first = runner(tmp_path, fake_server)
    assert first.returncode == 3, first.stderr
    status = json.loads((tmp_path / 'run/run_status.json').read_text())
    # item 4 hits the quota (after no retry), items 5-6 are never started
    assert status['status'] == 'INCOMPLETE' and status['stopped_by_quota'] and status['started'] == 4
    assert status['counts'] == {'done': 3, 'infra_error': 1, 'missing': 2} and Fake.calls == 4
    Fake.quota_after = None
    again = runner(tmp_path, fake_server)
    assert again.returncode != 0 and 'FileExistsError' in again.stderr   # never silently overwrite
    resumed = runner(tmp_path, fake_server, '--resume')
    assert resumed.returncode == 0, resumed.stderr
    status = json.loads((tmp_path / 'run/run_status.json').read_text())
    assert status['status'] == 'COMPLETE' and status['started'] == 3 and [m['id'] for m in status['superseded']] == ['q3']
    assert [h['status'] for h in status['history']] == ['INCOMPLETE', 'COMPLETE']
    assert (tmp_path / 'run/superseded/closed_book/q3__1/result.json').exists()
    assert Fake.calls == 4 + 3


def test_resume_refuses_a_changed_config(tmp_path, fake_server):
    Fake.quota_after = 1
    assert runner(tmp_path, fake_server).returncode == 3
    cfg = json.loads((tmp_path / 'config.json').read_text())
    cfg['budgets']['max_models'] = 2
    (tmp_path / 'config.json').write_text(json.dumps(cfg))
    questions = tmp_path / 'q.jsonl'
    env = {**os.environ, 'PYTHONPATH': 'src', 'LLM_API_KEY': 'test-key'}
    out = subprocess.run([sys.executable, 'scripts/run_agent_dev.py', '--questions', str(questions), '--config', str(tmp_path / 'config.json'),
                          '--url', fake_server, '--workers', '1', '--out', str(tmp_path / 'run'), '--resume'],
                         capture_output=True, text=True, env=env, cwd=Path(__file__).parents[1])
    assert out.returncode != 0 and 'config_sha256 differs' in out.stderr
