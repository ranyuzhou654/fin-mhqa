"""SQLite persistence for the service: users (hashed API keys), sessions, runs and per-run ordered events.

One connection guarded by a lock (WAL mode) is enough at this scale; the interface is small so a Postgres
implementation can replace it. API keys are shown once at creation and stored only as SHA-256 hashes.
"""
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
import threading
import time
import uuid

SCHEMA = '''
CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, name TEXT UNIQUE NOT NULL, key_hash TEXT UNIQUE NOT NULL,
  is_admin INTEGER NOT NULL DEFAULT 0, max_concurrent INTEGER NOT NULL, max_queued INTEGER NOT NULL,
  rate_per_min INTEGER NOT NULL, daily_tokens INTEGER NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, title TEXT, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, session_id TEXT NOT NULL, user_id TEXT NOT NULL, question TEXT NOT NULL,
  status TEXT NOT NULL, created REAL NOT NULL, started REAL, finished REAL, final TEXT, error TEXT, error_class TEXT,
  input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0, events INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS events (run_id TEXT NOT NULL, seq INTEGER NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL,
  ts REAL NOT NULL, PRIMARY KEY (run_id, seq));
CREATE INDEX IF NOT EXISTS runs_by_user ON runs (user_id, created);
CREATE INDEX IF NOT EXISTS runs_by_session ON runs (session_id, created);
'''
TERMINAL = ('succeeded', 'failed', 'cancelled', 'timed_out')


def key_hash(key):
    return hashlib.sha256(key.encode()).hexdigest()


class Store:
    def __init__(self, path=':memory:'):
        if path != ':memory:':
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.db.execute('PRAGMA journal_mode=WAL')
            self.db.executescript(SCHEMA)

    def _one(self, sql, *args):
        with self.lock:
            row = self.db.execute(sql, args).fetchone()
        return dict(row) if row else None

    def _all(self, sql, *args):
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def _exec(self, sql, *args):
        with self.lock:
            return self.db.execute(sql, args)

    # ---------- users ----------
    def create_user(self, name, is_admin=False, max_concurrent=2, max_queued=10, rate_per_min=20, daily_tokens=5_000_000):
        key = 'fmq_' + secrets.token_urlsafe(24)
        uid = 'u_' + uuid.uuid4().hex[:12]
        self._exec('INSERT INTO users VALUES (?,?,?,?,?,?,?,?,?)', uid, name, key_hash(key), int(is_admin),
                   max_concurrent, max_queued, rate_per_min, daily_tokens, time.time())
        return uid, key

    def user_by_key(self, key):
        return self._one('SELECT * FROM users WHERE key_hash=?', key_hash(key)) if key else None

    def user(self, uid):
        return self._one('SELECT * FROM users WHERE id=?', uid)

    # ---------- sessions ----------
    def create_session(self, user_id, title=None):
        sid = 's_' + uuid.uuid4().hex[:16]
        self._exec('INSERT INTO sessions VALUES (?,?,?,?)', sid, user_id, title, time.time())
        return self.session(sid)

    def session(self, sid):
        return self._one('SELECT * FROM sessions WHERE id=?', sid)

    def session_runs(self, sid):
        return [self._public_run(r) for r in self._all('SELECT * FROM runs WHERE session_id=? ORDER BY created', sid)]

    # ---------- runs ----------
    def create_run(self, session_id, user_id, question):
        rid = 'r_' + uuid.uuid4().hex[:16]
        self._exec('INSERT INTO runs (id, session_id, user_id, question, status, created) VALUES (?,?,?,?,?,?)',
                   rid, session_id, user_id, question, 'queued', time.time())
        return rid

    def user_runs(self, user_id, limit=30):
        rows = self._all('SELECT id, session_id, question, status, created, finished, final, error FROM runs '
                         'WHERE user_id=? ORDER BY created DESC LIMIT ?', user_id, limit)
        return [self._public_run(r) for r in rows]

    def run(self, rid):
        r = self._one('SELECT * FROM runs WHERE id=?', rid)
        return self._public_run(r) if r else None

    @staticmethod
    def _public_run(r):
        return {**r, 'final': json.loads(r['final']) if r.get('final') else None}

    def set_status(self, rid, status, **fields):
        now = time.time()
        extra = {'started': now} if status == 'running' else {'finished': now} if status in TERMINAL else {}
        extra.update(fields)
        if 'final' in extra:
            extra['final'] = json.dumps(extra['final'], ensure_ascii=False) if extra['final'] is not None else None
        cols = ', '.join(f'{k}=?' for k in ['status', *extra])
        # a terminal status is final: a late worker cannot overwrite a cancellation or a timeout
        cur = self._exec(f"UPDATE runs SET {cols} WHERE id=? AND status NOT IN ({','.join('?' * len(TERMINAL))})",
                         status, *extra.values(), rid, *TERMINAL)
        return cur.rowcount == 1

    def add_tokens(self, rid, usage):
        self._exec('UPDATE runs SET input_tokens=input_tokens+?, output_tokens=output_tokens+? WHERE id=?',
                   int(usage.get('input_tokens') or 0), int(usage.get('output_tokens') or 0), rid)

    def tokens_since(self, user_id, since):
        r = self._one('SELECT COALESCE(SUM(input_tokens+output_tokens),0) AS t FROM runs WHERE user_id=? AND created>=?', user_id, since)
        return r['t']

    def active_runs(self, user_id=None):
        sql = "SELECT id, user_id, status FROM runs WHERE status IN ('queued','running')"
        return self._all(sql + (' AND user_id=?' if user_id else ''), *([user_id] if user_id else []))

    def fail_unfinished(self, reason):
        """On restart, runs left queued/running by a dead process are marked failed, never silently resumed."""
        with self.lock:
            ids = [r[0] for r in self.db.execute("SELECT id FROM runs WHERE status IN ('queued','running')")]
        for rid in ids:
            self.set_status(rid, 'failed', error=reason, error_class='transport')
            self.append_event(rid, 'end', {'status': 'failed', 'error': reason})
        return ids

    # ---------- events ----------
    def append_event(self, rid, kind, data):
        with self.lock:
            seq = self.db.execute('SELECT COALESCE(MAX(seq),0)+1 FROM events WHERE run_id=?', (rid,)).fetchone()[0]
            self.db.execute('INSERT INTO events VALUES (?,?,?,?,?)', (rid, seq, kind, json.dumps(data, ensure_ascii=False), time.time()))
            self.db.execute('UPDATE runs SET events=? WHERE id=?', (seq, rid))
        return seq

    def events_after(self, rid, after=0, limit=500):
        rows = self._all('SELECT seq, kind, data, ts FROM events WHERE run_id=? AND seq>? ORDER BY seq LIMIT ?', rid, after, limit)
        return [{**r, 'data': json.loads(r['data'])} for r in rows]
