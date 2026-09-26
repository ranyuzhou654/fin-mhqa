"""Per-user admission control: request rate, queued+running runs, and a daily token quota.

Every refusal carries a Retry-After so well-behaved clients back off instead of hammering the server.
"""
from collections import deque
import threading
import time


class LimitExceeded(Exception):
    def __init__(self, status, reason, retry_after):
        super().__init__(reason)
        self.status, self.reason, self.retry_after = status, reason, max(1, int(retry_after))


class Limits:
    def __init__(self, store, clock=time.time):
        self.store, self.clock = store, clock
        self.lock = threading.Lock()
        self.recent = {}   # user_id -> deque of submit timestamps (sliding 60 s window)

    @staticmethod
    def day_start(now):
        return now - (now % 86400)   # UTC day

    def check_submit(self, user):
        now = self.clock()
        with self.lock:
            window = self.recent.setdefault(user['id'], deque())
            while window and now - window[0] >= 60:
                window.popleft()
            if len(window) >= user['rate_per_min']:
                raise LimitExceeded(429, f"rate limit: {user['rate_per_min']} questions per minute", 60 - (now - window[0]))
            active = len(self.store.active_runs(user['id']))
            if active >= user['max_queued']:
                raise LimitExceeded(429, f"too many unfinished runs ({active}/{user['max_queued']})", 5)
            used = self.store.tokens_since(user['id'], self.day_start(now))
            if used >= user['daily_tokens']:
                raise LimitExceeded(429, f"daily token quota used ({used}/{user['daily_tokens']})", self.day_start(now) + 86400 - now)
            window.append(now)
