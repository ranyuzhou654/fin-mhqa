"""Frozen benchmark_v1 access: hash-checked questions, and a Test split that only registered configs may run."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

FROZEN = Path('data/bench/benchmark_v1')
GOLD = Path('data/bench/benchmark_v1_gold')
REGISTRY = Path('configs/benchmark_v1_registry.json')


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_questions(split, *, final=False, config=None, frozen=FROZEN, registry=REGISTRY):
    """Question-only rows of a frozen split. Test additionally requires --final and a registered config."""
    manifest = json.loads((frozen / 'manifest.json').read_text())
    path = frozen / f'{split}_questions.jsonl'
    if sha256(path) != manifest['files'][f'{split}_questions.jsonl']:
        raise ValueError(f'{split} questions differ from the frozen manifest')
    if split == 'test':
        if not final:
            raise PermissionError('Test runs require --final; tune and compare on Dev')
        if config is None:
            raise PermissionError('Test runs require a registered --config')
        registered = {e['sha256']: e for e in json.loads(Path(registry).read_text())['registered']}
        if sha256(config) not in registered:
            raise PermissionError(f'config {config} (sha256 {sha256(config)[:12]}) is not registered for Test')
    elif final:
        raise ValueError('--final only applies to the Test split')
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    return rows, {'benchmark': manifest['name'], 'split': split, 'questions_sha256': sha256(path),
                  'manifest_sha256': sha256(frozen / 'manifest.json')}


def log_test_run(event, **fields):
    """Append-only record of every Test run attempt (started and finished)."""
    with (FROZEN / 'test_runs.jsonl').open('a') as f:
        f.write(json.dumps({'event': event, 'at': datetime.now(timezone.utc).isoformat(timespec='seconds'), **fields},
                           ensure_ascii=False) + '\n')
