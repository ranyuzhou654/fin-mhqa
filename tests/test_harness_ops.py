"""Harness operations: cross-process retrieval lock, provenance, cost estimate, registry categories, canary."""
import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
import time
import pytest
from finmhqa.harness.provenance import git_state, retrieval_lock
from test_harness import Fake, fake_server  # noqa: F401  (fixture reused)

ROOT = Path(__file__).parents[1]


def hold(lock_path, log_path, name):
    with retrieval_lock(lock_path):
        with open(log_path, 'a') as f:
            f.write(f'{name} start {time.monotonic()}\n')
        time.sleep(0.2)
        with open(log_path, 'a') as f:
            f.write(f'{name} end {time.monotonic()}\n')


def test_retrieval_lock_serializes_processes(tmp_path):
    procs = [multiprocessing.Process(target=hold, args=(tmp_path / 'lock', tmp_path / 'log', n)) for n in 'abc']
    for p in procs:
        p.start()
    for p in procs:
        p.join()
    lines = [l.split() for l in (tmp_path / 'log').read_text().splitlines()]
    # every start is immediately followed by the same holder's end: no two holders overlap
    assert len(lines) == 6 and all(lines[i][0] == lines[i + 1][0] and lines[i][1] == 'start' for i in range(0, 6, 2))


def test_git_state_records_commit_and_dirty_flag():
    g = git_state(ROOT)
    assert len(g['commit']) == 40 and isinstance(g['dirty'], bool)


def test_cost_estimate_prefers_same_config_and_enforces_limit(tmp_path):
    registry = tmp_path / 'r.json'
    registry.write_text(json.dumps([
        {'category': 'valid', 'config': 'configs/bench_b2_flat_repair.json', 'arm': 'flat', 'tokens_per_item': 50000},
        {'category': 'valid', 'config': 'configs/bench_b2_flat_repair.json', 'arm': 'flat', 'tokens_per_item': 70000},
        {'category': 'invalid_quota', 'config': 'configs/bench_b2_flat_repair.json', 'arm': 'flat', 'tokens_per_item': 1},
        {'category': 'valid', 'config': 'configs/other.json', 'arm': 'flat', 'tokens_per_item': 10}]))
    run = lambda *extra: subprocess.run([sys.executable, 'scripts/cost_estimate.py', '--config', 'configs/bench_b2_flat_repair.json',
                                         '--items', '10', '--registry', str(registry), *extra], capture_output=True, text=True, cwd=ROOT)
    ok = run()
    assert ok.returncode == 0 and json.loads(ok.stdout)['estimate_tokens'] == 600000 and json.loads(ok.stdout)['basis'] == 'same config'
    assert run('--limit-tokens', '500000').returncode == 1


def test_registry_categories():
    sys.path.insert(0, str(ROOT / 'scripts'))
    from run_registry import category
    assert [category(n) for n in ('dev_b2_flat_r1', 'invalid_quota_dev_x', 'invalid_dev_x', 'aborted_dev_x', 'superseded_zen_dev_x')] == \
        ['valid', 'invalid_quota', 'invalid', 'aborted', 'superseded_zen']


FROZEN_DEV = ROOT / 'data/bench/benchmark_v1/dev_questions.jsonl'


@pytest.mark.skipif(not FROZEN_DEV.exists(), reason='frozen benchmark data is local only (not in git)')
@pytest.mark.parametrize('quota_after,expected', [(None, 1), (2, 3)])
def test_canary_flags_drift_and_stops_on_quota(tmp_path, fake_server, quota_after, expected):
    Fake.quota_after = quota_after   # the fake model always answers "insufficient": a drifted provider
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'teacher': {'backend': 'openai', 'base_url': fake_server + '/v1', 'model': 'fake'}, 'arm': 'closed_book',
                                  'synthesis': 'verified', 'context_index': None, 'reserve_verifier': False,
                                  'budgets': {'max_models': 1, 'max_tools': 1, 'max_searches': 1, 'max_replans': 0}}))
    out = subprocess.run([sys.executable, 'scripts/canary.py', '--config', str(config), '--url', fake_server, '--out', str(tmp_path / 'c')],
                         capture_output=True, text=True, cwd=ROOT, env={**os.environ, 'PYTHONPATH': 'src', 'LLM_API_KEY': 'k'})
    assert out.returncode == expected, out.stdout + out.stderr
    if expected == 1:
        report = json.loads((tmp_path / 'c/canary_report.json').read_text())
        assert report['verdict'] == 'DRIFT_SUSPECTED' and report['correct'] == 0 and len(report['items']) == 5
