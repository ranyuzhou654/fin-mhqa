"""Run harness: error classes, fail-fast on exhausted quota, run status, and per-item resume.

Rules (docs/system/HARNESS.md):
- quota / billing errors stop the whole batch at once; remaining items are not started;
- only infrastructure failures (quota, transport) are rerun on --resume; an agent failure is a result;
- a run with missing items or remaining infrastructure failures is INCOMPLETE and is not scored by default.
"""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import threading
import time

EXIT_OK, EXIT_INCOMPLETE, EXIT_QUOTA = 0, 4, 3
INFRA = ('quota', 'transport')
# Substrings of error messages as recorded in result.json ("Type: message"); checked in this order.
QUOTA_MARKERS = ('QuotaExhausted', 'GoUsageLimitError', 'usage limit exceeded', 'Insufficient account funds', 'HTTP 402')
TRANSPORT_MARKERS = ('teacher transport failed', 'IncompleteRead', 'RemoteDisconnected', 'Remote end closed',
                     'ConnectionResetError', 'URLError', 'TimeoutError', 'timed out', 'Inference busy', 'HTTP 5')


class QuotaExhausted(RuntimeError):
    """The model provider refuses further requests for billing or quota reasons; retrying cannot help."""


def classify_error(message):
    """None for no error, else 'quota', 'transport' (both infrastructure) or 'agent'."""
    if not message:
        return None
    if any(m in message for m in QUOTA_MARKERS):
        return 'quota'
    if any(m in message for m in TRANSPORT_MARKERS):
        return 'transport'
    return 'agent'


def is_quota_response(code, body):
    """HTTP 402, or a 429 whose body names a usage/billing limit (a plain rate limit stays retryable)."""
    text = (body or '').lower()
    return code == 402 or (code == 429 and any(k in text for k in ('usagelimit', 'usage limit', 'quota', 'insufficient', 'billing')))


def item_state(folder):
    """'done' (result, no infra error), 'agent_error', 'infra_error', 'partial' (started, no result) or 'missing'."""
    folder = Path(folder)
    if not folder.exists():
        return 'missing'
    result = folder / 'result.json'
    if not result.exists():
        return 'partial'
    cls = classify_error(json.loads(result.read_text()).get('error'))
    return 'infra_error' if cls in INFRA else 'agent_error' if cls else 'done'


def plan_resume(out, tasks):
    """Move partial / infra-failed item folders aside and return the tasks still to run.

    tasks: list of (arm, row). Superseded folders are kept under out/superseded/<arm>/<id>__<n> as evidence;
    they sit outside the arm folder so audits and scoring never read them.
    """
    out = Path(out)
    todo, log = [], []
    for arm, row in tasks:
        folder = out / arm / row['id']
        state = item_state(folder)
        if state in ('done', 'agent_error'):
            continue
        if state in ('partial', 'infra_error'):
            n = 1
            while (out / 'superseded' / arm / f"{row['id']}__{n}").exists():
                n += 1
            target = out / 'superseded' / arm / f"{row['id']}__{n}"
            target.parent.mkdir(parents=True, exist_ok=True)
            folder.rename(target)
            log.append({'id': row['id'], 'arm': arm, 'state': state, 'moved_to': str(target.relative_to(out))})
        todo.append((arm, row))
    return todo, log


def execute(tasks, run_one, workers):
    """Run tasks in a pool; run_one returns the item's error class. A quota error stops unstarted tasks.

    Returns (started, stopped_by_quota).
    """
    stop = threading.Event()
    started = []
    lock = threading.Lock()

    def guarded(task):
        if stop.is_set():
            return
        with lock:
            started.append(task)
        if run_one(task) == 'quota':
            stop.set()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(guarded, tasks))
    return started, stop.is_set()


def summarize_run(out, tasks):
    """Status of a run folder from its items: COMPLETE only if every item is done or an agent error."""
    states = {}
    for arm, row in tasks:
        states.setdefault(item_state(Path(out) / arm / row['id']), []).append(row['id'])
    counts = {k: len(v) for k, v in states.items()}
    complete = set(states) <= {'done', 'agent_error'}
    return {'status': 'COMPLETE' if complete else 'INCOMPLETE', 'items': len(tasks), 'counts': counts,
            'unfinished': sorted(i for k in ('missing', 'partial', 'infra_error') for i in states.get(k, []))}


def write_status(out, status, **extra):
    path = Path(out) / 'run_status.json'
    history = json.loads(path.read_text())['history'] if path.exists() else []
    record = {**status, **extra, 'at': time.strftime('%Y-%m-%dT%H:%M:%S')}
    path.write_text(json.dumps({**record, 'history': history + [record]}, ensure_ascii=False, indent=2) + '\n')
    return record
