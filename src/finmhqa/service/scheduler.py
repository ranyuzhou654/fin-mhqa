"""Run scheduling: a worker pool with per-user fair queues, cooperative cancellation, timeouts, and event fan-out.

- Fairness: users are served round-robin; a user never has more than max_concurrent runs executing, so one heavy
  user cannot occupy every worker.
- Cancellation / timeout: the run becomes terminal at once and an 'end' event is published immediately; the worker
  stops at the agent's next event (a model call already in flight finishes first, then its result is discarded).
- Events: every agent event is persisted with a per-run sequence number, then waiting SSE streams are woken.
"""
import asyncio
from collections import deque
import inspect
import threading
import time
from finmhqa.harness.runs import classify_error
from finmhqa.service.store import TERMINAL


class Cancelled(Exception):
    pass


class QueueFull(Exception):
    pass


class Broadcaster:
    """Wake asyncio waiters (SSE streams) from worker threads."""

    def __init__(self):
        self.lock = threading.Lock()
        self.waiters = {}

    def subscribe(self, rid, loop):
        ev = asyncio.Event()
        with self.lock:
            self.waiters.setdefault(rid, set()).add((loop, ev))
        return ev

    def unsubscribe(self, rid, loop, ev):
        with self.lock:
            self.waiters.get(rid, set()).discard((loop, ev))

    def notify(self, rid):
        with self.lock:
            targets = list(self.waiters.get(rid, ()))
        for loop, ev in targets:
            loop.call_soon_threadsafe(ev.set)


class RunContext:
    def __init__(self, rid, user_id, question, context=None):
        self.rid, self.user_id, self.question = rid, user_id, question
        self.context = context   # earlier turns, strategy and preferences (None for a bare question)
        self.cancel = threading.Event()
        self.reason = None
        self.started = None


class Scheduler:
    def __init__(self, store, factory, bus=None, metrics=None, workers=4, max_queue=100, run_timeout=900, log=None, policy='fair'):
        self.store, self.factory, self.bus, self.metrics, self.log = store, factory, bus or Broadcaster(), metrics, log
        self.workers, self.max_queue, self.run_timeout = workers, max_queue, run_timeout
        if policy not in ('fair', 'fifo'):
            raise ValueError('policy must be fair or fifo')
        self.policy = policy   # 'fifo' exists as the load-test control: one global queue, no per-user limit
        self.seq = 0
        self.cond = threading.Condition()
        self.queues = {}          # user_id -> deque[RunContext]
        self.order = deque()      # round-robin over users with queued work
        self.running = {}         # rid -> RunContext
        self.per_user = {}        # user_id -> running count
        self.stopping = False
        self.threads = []

    # ---------- lifecycle ----------
    def start(self):
        for i in range(self.workers):
            t = threading.Thread(target=self._worker, name=f'agent-worker-{i}', daemon=True)
            t.start()
            self.threads.append(t)
        threading.Thread(target=self._watchdog, name='run-watchdog', daemon=True).start()

    def stop(self, timeout=5):
        with self.cond:
            self.stopping = True
            running = list(self.running.values())
            queued = [c for q in self.queues.values() for c in q]
            self.cond.notify_all()
        for ctx in running + queued:
            self.cancel(ctx.rid, 'cancelled', why='server shutdown')
        for t in self.threads:
            t.join(timeout)

    # ---------- submission and control ----------
    def queue_depth(self):
        with self.cond:
            return sum(len(q) for q in self.queues.values())

    def running_count(self):
        with self.cond:
            return len(self.running)

    def submit(self, rid, user_id, question, context=None):
        with self.cond:
            if sum(len(q) for q in self.queues.values()) >= self.max_queue:
                raise QueueFull('server queue is full')
            ctx = RunContext(rid, user_id, question, context)
            self.seq += 1
            ctx.seq = self.seq
            self.queues.setdefault(user_id, deque()).append(ctx)
            if user_id not in self.order:
                self.order.append(user_id)
            self.cond.notify()
        self._publish(ctx, 'status', {'status': 'queued'})
        return ctx

    def cancel(self, rid, reason='cancelled', why='cancelled by user'):
        with self.cond:
            ctx = self.running.get(rid)
            if ctx is None:
                for q in self.queues.values():
                    for c in q:
                        if c.rid == rid:
                            ctx = c
                            q.remove(c)
                            break
                    if ctx:
                        break
        if ctx is None or not self.store.set_status(rid, reason, error=why):
            return False
        ctx.reason = reason
        ctx.cancel.set()
        self._publish(ctx, 'end', {'status': reason, 'error': why}, force=True)
        if self.metrics:
            self.metrics.inc('runs_finished_total', status=reason)
        return True

    # ---------- internals ----------
    def _publish(self, ctx, kind, data, force=False):
        if ctx.cancel.is_set() and not force:
            raise Cancelled()
        self.store.append_event(ctx.rid, kind, data)
        self.bus.notify(ctx.rid)

    def _next(self):
        """Round-robin over users; skip users already at their concurrency limit (fifo: oldest run first)."""
        if self.policy == 'fifo':
            heads = [q[0] for q in self.queues.values() if q]
            if not heads:
                return None
            ctx = min(heads, key=lambda c: c.seq)
            self.queues[ctx.user_id].popleft()
            if not self.queues[ctx.user_id] and ctx.user_id in self.order:
                self.order.remove(ctx.user_id)
            return ctx
        for _ in range(len(self.order)):
            uid = self.order[0]
            self.order.rotate(-1)
            q = self.queues.get(uid)
            if not q:
                continue
            limit = (self.store.user(uid) or {}).get('max_concurrent', 1)
            if self.per_user.get(uid, 0) >= limit:
                continue
            ctx = q.popleft()
            if not q:
                self.order.remove(uid)
            return ctx
        return None

    def _worker(self):
        while True:
            with self.cond:
                ctx = self._next()
                while ctx is None and not self.stopping:
                    self.cond.wait(0.5)
                    ctx = self._next()
                if self.stopping and ctx is None:
                    return
                self.running[ctx.rid] = ctx
                self.per_user[ctx.user_id] = self.per_user.get(ctx.user_id, 0) + 1
            try:
                self._execute(ctx)
            finally:
                with self.cond:
                    self.running.pop(ctx.rid, None)
                    self.per_user[ctx.user_id] -= 1
                    self.cond.notify_all()

    def _execute(self, ctx):
        if ctx.cancel.is_set() or not self.store.set_status(ctx.rid, 'running'):
            return
        ctx.started = time.monotonic()
        started = time.time()
        if self.log:
            self.log('run_started', run_id=ctx.rid, user_id=ctx.user_id)
        try:
            self._publish(ctx, 'status', {'status': 'running'})

            def emit(event):
                if event.get('event') == 'teacher_decision':
                    self.store.add_tokens(ctx.rid, event.get('usage') or {})
                    if self.metrics:
                        self.metrics.inc('model_calls_total', role=event.get('role', 'model'))
                        self.metrics.observe('model_call_seconds', event.get('seconds') or 0, role=event.get('role', 'model'))
                self._publish(ctx, event.get('event', 'event'), event)
            # agents that take the conversation get it; older agents (and test fakes) keep the 4-argument call
            takes_context = 'context' in inspect.signature(self.factory.run).parameters
            result = (self.factory.run(ctx.question, emit, ctx.cancel.is_set, ctx.rid, context=ctx.context) if takes_context
                      else self.factory.run(ctx.question, emit, ctx.cancel.is_set, ctx.rid))
            final = result.get('final')
            if self.store.set_status(ctx.rid, 'succeeded', final=final, standalone=result.get('standalone'),
                                     evidence=result.get('evidence') or None):
                self._publish(ctx, 'end', {'status': 'succeeded', 'final': final}, force=True)
                status = 'succeeded'
            else:
                status = ctx.reason
        except Cancelled:
            status = ctx.reason
        except Exception as exc:
            error = f'{type(exc).__name__}: {exc}'
            if ctx.cancel.is_set():
                status = ctx.reason
            elif self.store.set_status(ctx.rid, 'failed', error=error[:2000], error_class=classify_error(error)):
                self._publish(ctx, 'end', {'status': 'failed', 'error': error[:500], 'error_class': classify_error(error)}, force=True)
                status = 'failed'
            else:
                status = ctx.reason
        if self.metrics and status in ('succeeded', 'failed'):
            self.metrics.inc('runs_finished_total', status=status)
            self.metrics.observe('run_seconds', time.time() - started)
        if self.log:
            self.log('run_finished', run_id=ctx.rid, user_id=ctx.user_id, status=status, seconds=round(time.time() - started, 3))

    def _watchdog(self):
        while not self.stopping:
            time.sleep(0.2)
            with self.cond:
                late = [c for c in self.running.values() if c.started and time.monotonic() - c.started > self.run_timeout]
            for ctx in late:
                self.cancel(ctx.rid, 'timed_out', why=f'run exceeded {self.run_timeout}s')
