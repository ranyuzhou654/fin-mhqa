"""Service load test with recorded trajectories (no model calls): latency, SSE integrity, reconnects, fairness.

Each simulated user is an async HTTP client that submits questions and consumes the SSE stream like a browser.
The agent replays real recorded Dev trajectories at their recorded timings multiplied by --speed, so the numbers
measure the service layer (queueing, workers, persistence, fan-out), not model or retrieval throughput.
"""
import argparse
import asyncio
import glob
import json
from pathlib import Path
import socket
import statistics
import threading
import time
import httpx
import uvicorn
from finmhqa.service.agents import CassetteAgent
from finmhqa.service.app import create_app
from finmhqa.service.observability import Metrics
from finmhqa.service.scheduler import Scheduler
from finmhqa.service.store import Store


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class Server:
    def __init__(self, cases, speed, workers, policy):
        self.store = Store()
        self.agent = CassetteAgent(cases, speed=speed)
        self.scheduler = Scheduler(self.store, self.agent, metrics=Metrics(), workers=workers, max_queue=1000, policy=policy)
        self.port = free_port()
        self.server = uvicorn.Server(uvicorn.Config(create_app(self.store, self.scheduler, self.scheduler.metrics, heartbeat=5),
                                                    host='127.0.0.1', port=self.port, log_level='warning'))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        while not self.server.started:
            time.sleep(0.05)
        return self

    def __exit__(self, *exc):
        self.server.should_exit = True
        self.thread.join(10)


async def one_run(http, key, sid, question, drop_after=None):
    h = {'Authorization': f'Bearer {key}'}
    t0 = time.monotonic()
    r = await http.post(f'/sessions/{sid}/questions', json={'question': question}, headers=h)
    if r.status_code != 202:
        return {'rejected': r.status_code}
    rid = r.json()['run_id']
    ids, first, running_at, status, reconnects, last = [], None, None, None, 0, 0
    while status is None:
        headers = {**h, **({'Last-Event-ID': str(last)} if last else {})}
        async with http.stream('GET', f'/runs/{rid}/events?detail=summary', headers=headers) as s:
            block = {}
            async for line in s.aiter_lines():
                if line.startswith('id: '):
                    block['id'] = int(line[4:])
                elif line.startswith('event: '):
                    block['event'] = line[7:]
                elif line.startswith('data: '):
                    block['data'] = json.loads(line[6:])
                elif line == '' and 'id' in block:
                    now = time.monotonic()
                    first = first or now
                    ids.append(block['id'])
                    last = block['id']
                    if block['event'] == 'status' and block['data']['status'] == 'running':
                        running_at = now
                    if block['event'] == 'end':
                        status = block['data']['status']
                        break
                    block = {}
                    if drop_after and reconnects == 0 and len(ids) == drop_after:
                        reconnects += 1
                        break      # drop the connection mid-run, then resume with Last-Event-ID
    end = time.monotonic()
    stored = (await http.get(f'/runs/{rid}', headers=h)).json()['events']
    return {'run_id': rid, 'status': status, 'submit_to_first_event': first - t0, 'queue_wait': (running_at or end) - t0,
            'submit_to_end': end - t0, 'events': len(ids), 'stored_events': stored, 'contiguous': ids == list(range(1, len(ids) + 1)),
            'reconnects': reconnects}


def pct(values, q):
    values = sorted(values)
    return round(values[min(len(values) - 1, int(round(q * (len(values) - 1))))], 3) if values else None


def describe(rows):
    ok = [r for r in rows if 'run_id' in r]
    return {'runs': len(rows), 'succeeded': sum(r.get('status') == 'succeeded' for r in ok),
            'rejected': sum('rejected' in r for r in rows),
            'events_lost_or_duplicated': sum((not r['contiguous']) or r['events'] != r['stored_events'] for r in ok),
            'reconnected_runs': sum(r['reconnects'] for r in ok),
            'submit_to_first_event_p50': pct([r['submit_to_first_event'] for r in ok], 0.5),
            'submit_to_first_event_p95': pct([r['submit_to_first_event'] for r in ok], 0.95),
            'queue_wait_p50': pct([r['queue_wait'] for r in ok], 0.5), 'queue_wait_p95': pct([r['queue_wait'] for r in ok], 0.95),
            'submit_to_end_p50': pct([r['submit_to_end'] for r in ok], 0.5), 'submit_to_end_p95': pct([r['submit_to_end'] for r in ok], 0.95)}


async def scenario_load(server, questions, users, per_user):
    keys = [server.store.create_user(f'user{i}', max_concurrent=2, max_queued=per_user + 1, rate_per_min=1000)[1] for i in range(users)]
    async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{server.port}', timeout=None) as http:
        sids = [(await http.post('/sessions', json={}, headers={'Authorization': f'Bearer {k}'})).json()['id'] for k in keys]
        t0 = time.monotonic()
        jobs = [one_run(http, k, sid, questions[(u * per_user + j) % len(questions)], drop_after=5 if (u + j) % 3 == 0 else None)
                for u, (k, sid) in enumerate(zip(keys, sids)) for j in range(per_user)]
        rows = await asyncio.gather(*jobs)
        wall = time.monotonic() - t0
    return {**describe(rows), 'wall_seconds': round(wall, 2), 'throughput_runs_per_min': round(len(rows) / wall * 60, 1)}


async def scenario_fairness(server, questions, heavy_runs, light_users, light_runs):
    store = server.store
    heavy = store.create_user('heavy', max_concurrent=2, max_queued=heavy_runs + 1, rate_per_min=1000)[1]
    lights = [store.create_user(f'light{i}', max_concurrent=2, max_queued=light_runs + 1, rate_per_min=1000)[1] for i in range(light_users)]
    async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{server.port}', timeout=None) as http:
        async def session(k):
            return (await http.post('/sessions', json={}, headers={'Authorization': f'Bearer {k}'})).json()['id']
        hs = await session(heavy)
        heavy_jobs = [asyncio.create_task(one_run(http, heavy, hs, questions[i % len(questions)])) for i in range(heavy_runs)]
        await asyncio.sleep(0.5)   # the heavy user's burst is already queued when the light users arrive
        light_jobs = []
        for k in lights:
            ls = await session(k)
            light_jobs += [asyncio.create_task(one_run(http, k, ls, questions[(7 + j) % len(questions)])) for j in range(light_runs)]
        heavy_rows, light_rows = await asyncio.gather(asyncio.gather(*heavy_jobs), asyncio.gather(*light_jobs))
    return {'heavy': describe(heavy_rows), 'light': describe(light_rows)}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cassettes', default='data/bench/runs/dev_b3_graph_repair_verifier_v2_r1/structured/*')
    p.add_argument('--speed', type=float, default=0.05)
    p.add_argument('--workers', type=int, default=4)
    p.add_argument('--users', type=int, default=8)
    p.add_argument('--per-user', type=int, default=3)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    cases = sorted(c for c in glob.glob(a.cassettes) if (Path(c) / 'trajectory.jsonl').exists() and
                   json.loads((Path(c) / 'result.json').read_text()).get('error') is None)
    questions = [json.loads((Path(c) / 'trajectory.jsonl').read_text().splitlines()[0])['question'] for c in cases]
    recorded = [sum(e.get('seconds') or 0 for e in map(json.loads, (Path(c) / 'trajectory.jsonl').read_text().splitlines())) for c in cases]
    report = {'cassettes': len(cases), 'speed': a.speed, 'workers': a.workers,
              'replayed_run_seconds_mean': round(statistics.mean(recorded) * a.speed, 2),
              'scope': 'service layer only: recorded trajectories replayed at recorded timing x speed; no model or retrieval calls'}
    with Server(cases, a.speed, a.workers, 'fair') as s:
        report['load'] = asyncio.run(scenario_load(s, questions, a.users, a.per_user))
        report['load']['users'], report['load']['runs_per_user'] = a.users, a.per_user
    for policy in ('fair', 'fifo'):
        with Server(cases, a.speed, a.workers, policy) as s:
            report[f'fairness_{policy}'] = asyncio.run(scenario_fairness(s, questions, heavy_runs=16, light_users=4, light_runs=2))
    a.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
