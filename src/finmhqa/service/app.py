"""HTTP API with Server-Sent Events for live agent runs (roadmap P3).

Auth: `Authorization: Bearer <api key>`. Every session and run belongs to one user; another user's resources
answer 404, never 403, so their existence is not revealed.
"""
import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import re
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field
from finmhqa.service.limits import LimitExceeded, Limits
from finmhqa.service.scheduler import QueueFull
from finmhqa.service.store import TERMINAL

STATIC = Path(__file__).parent / 'static'
CONTROL = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')


class SessionIn(BaseModel):
    title: str | None = Field(default=None, max_length=100)


class QuestionIn(BaseModel):
    question: str = Field(min_length=4, max_length=500)


def summarize_event(kind, data):
    """SSE 'summary' detail: tool responses keep chunk labels, not full evidence text."""
    if kind == 'tool_response' and isinstance(data.get('response'), dict) and 'results' in data['response']:
        r = data['response']
        labels = [{k: c.get(k) for k in ('chunk_id', 'chunk_type', 'page_start', 'table_title') if c.get(k) is not None}
                  for c in r['results']]
        return {**data, 'response': {'results': labels, 'result_count': len(labels),
                                     **{k: v for k, v in r.items() if k in ('next_cursor', 'total_candidates', 'error')}}}
    return data


def create_app(store, scheduler, metrics, heartbeat=15.0, log=None):
    limits = Limits(store)

    @asynccontextmanager
    async def lifespan(app):
        stale = store.fail_unfinished('server restarted before the run finished')
        if log and stale:
            log('stale_runs_failed', count=len(stale))
        scheduler.start()
        yield
        scheduler.stop()

    app = FastAPI(title='fin-mhqa agent service', version='0.1', lifespan=lifespan)
    metrics.gauge('queue_depth', scheduler.queue_depth)
    metrics.gauge('runs_executing', scheduler.running_count)
    sse_open = {'n': 0}
    metrics.gauge('sse_connections', lambda: sse_open['n'])

    def user_of(authorization: str | None = Header(default=None)):
        key = authorization[7:] if authorization and authorization.startswith('Bearer ') else None
        user = store.user_by_key(key)
        if user is None:
            raise HTTPException(401, 'missing or invalid API key', headers={'WWW-Authenticate': 'Bearer'})
        return user

    def owned_session(sid, user):
        s = store.session(sid)
        if s is None or s['user_id'] != user['id']:
            raise HTTPException(404, 'session not found')
        return s

    def owned_run(rid, user):
        r = store.run(rid)
        if r is None or r['user_id'] != user['id']:
            raise HTTPException(404, 'run not found')
        return r

    @app.get('/', response_class=HTMLResponse)
    def index():
        return (STATIC / 'index.html').read_text()

    @app.get('/healthz')
    def healthz():
        return {'status': 'ok', 'workers': scheduler.workers, 'queue_depth': scheduler.queue_depth(),
                'executing': scheduler.running_count()}

    @app.get('/examples')
    def examples():
        """Example questions the current agent can answer (for cassette replay: questions that have a recording)."""
        fn = getattr(scheduler.factory, 'examples', None)
        return {'examples': fn() if fn else []}

    @app.get('/metrics', response_class=PlainTextResponse)
    def get_metrics(user=Depends(user_of)):
        if not user['is_admin']:
            raise HTTPException(404, 'not found')
        return metrics.render()

    @app.post('/sessions', status_code=201)
    def create_session(body: SessionIn, user=Depends(user_of)):
        return store.create_session(user['id'], body.title)

    @app.get('/sessions/{sid}')
    def get_session(sid: str, user=Depends(user_of)):
        return {**owned_session(sid, user), 'runs': store.session_runs(sid)}

    @app.post('/sessions/{sid}/questions', status_code=202)
    def ask(sid: str, body: QuestionIn, user=Depends(user_of)):
        owned_session(sid, user)
        question = CONTROL.sub('', body.question).strip()
        if len(question) < 4:
            raise HTTPException(422, 'question is empty after removing control characters')
        try:
            limits.check_submit(user)
        except LimitExceeded as e:
            metrics.inc('rejected_total', reason='user_limit')
            return JSONResponse({'detail': e.reason}, status_code=e.status, headers={'Retry-After': str(e.retry_after)})
        if scheduler.queue_depth() >= scheduler.max_queue:
            metrics.inc('rejected_total', reason='queue_full')
            return JSONResponse({'detail': 'server busy'}, status_code=503, headers={'Retry-After': '10'})
        rid = store.create_run(sid, user['id'], question)
        try:
            scheduler.submit(rid, user['id'], question)
        except QueueFull:
            store.set_status(rid, 'failed', error='server queue full', error_class='transport')
            return JSONResponse({'detail': 'server busy'}, status_code=503, headers={'Retry-After': '10'})
        metrics.inc('runs_submitted_total')
        if log:
            log('run_submitted', run_id=rid, user_id=user['id'], session_id=sid)
        return JSONResponse({'run_id': rid, 'status': 'queued', 'events': f'/runs/{rid}/events'}, status_code=202,
                            headers={'Location': f'/runs/{rid}'})

    @app.get('/runs')
    def list_runs(limit: int = 30, user=Depends(user_of)):
        """The caller's most recent runs, newest first (history sidebar)."""
        return {'runs': store.user_runs(user['id'], max(1, min(limit, 100)))}

    @app.get('/runs/{rid}')
    def get_run(rid: str, user=Depends(user_of)):
        return owned_run(rid, user)

    @app.delete('/runs/{rid}', status_code=202)
    def cancel_run(rid: str, user=Depends(user_of)):
        owned_run(rid, user)
        return {'run_id': rid, 'cancelled': scheduler.cancel(rid, 'cancelled', why='cancelled by user')}

    @app.get('/runs/{rid}/trajectory')
    def trajectory(rid: str, user=Depends(user_of)):
        owned_run(rid, user)
        return {'run_id': rid, 'events': store.events_after(rid, 0, limit=100000)}

    @app.get('/runs/{rid}/events')
    async def events(rid: str, request: Request, detail: str = 'summary', after: int = 0,
                     last_event_id: str | None = Header(default=None), user=Depends(user_of)):
        owned_run(rid, user)
        if detail not in ('summary', 'full'):
            raise HTTPException(422, 'detail must be summary or full')
        start = int(last_event_id) if last_event_id and last_event_id.isdigit() else after
        loop = asyncio.get_running_loop()

        async def stream():
            last = start
            wake = scheduler.bus.subscribe(rid, loop)
            sse_open['n'] += 1
            try:
                yield f'retry: 2000\n: connected to {rid}\n\n'
                while True:
                    wake.clear()
                    batch = await asyncio.to_thread(store.events_after, rid, last)
                    for e in batch:
                        data = e['data'] if detail == 'full' else summarize_event(e['kind'], e['data'])
                        yield f"id: {e['seq']}\nevent: {e['kind']}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
                        last = e['seq']
                        if e['kind'] == 'end':
                            return
                    if not batch:
                        run = await asyncio.to_thread(store.run, rid)
                        if run['status'] in TERMINAL and not await asyncio.to_thread(store.events_after, rid, last):
                            return
                        if await request.is_disconnected():
                            return
                        try:
                            await asyncio.wait_for(wake.wait(), timeout=heartbeat)
                        except asyncio.TimeoutError:
                            yield ': ping\n\n'
            finally:
                sse_open['n'] -= 1
                scheduler.bus.unsubscribe(rid, loop, wake)

        return StreamingResponse(stream(), media_type='text/event-stream',
                                 headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no', 'Connection': 'keep-alive'})

    return app
