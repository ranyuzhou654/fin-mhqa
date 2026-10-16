"""SQLite persistence for the service: users (hashed API keys), sessions, runs, per-run ordered events and
per-user preferences.

A session is a conversation: its runs are the turns, in order. A finished turn keeps the question as typed, the
standalone question the agent actually answered (after rewriting) and the chunks its answer cited, so the next
turn can be given the conversation (docs/benchmark/BENCHMARK_V2_DESIGN.md).

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
CREATE TABLE IF NOT EXISTS preferences (user_id TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, updated REAL NOT NULL,
  PRIMARY KEY (user_id, key));
CREATE INDEX IF NOT EXISTS runs_by_user ON runs (user_id, created);
CREATE INDEX IF NOT EXISTS runs_by_session ON runs (session_id, created);
'''
TERMINAL = ('succeeded', 'failed', 'cancelled', 'timed_out')
# columns added after the first release; created on open when an older database lacks them
MIGRATIONS = {'sessions': [('strategy', "TEXT NOT NULL DEFAULT 'S3'")],
              'runs': [('standalone', 'TEXT'), ('evidence', 'TEXT')]}
STRATEGIES = {'S1': '每轮独立', 'S2': '附对话记录', 'S3': '先改写成完整问题', 'S4': '改写并延续已核验证据'}
PREFERENCES = {'unit': ('亿元', '万元', '元'), 'decimals': ('0', '1', '2', '3', '4')}   # plus 'watchlist': company names


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
            for table, cols in MIGRATIONS.items():
                have = {r[1] for r in self.db.execute(f'PRAGMA table_info({table})')}
                for name, decl in cols:
                    if name not in have:
                        self.db.execute(f'ALTER TABLE {table} ADD COLUMN {name} {decl}')

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
    def create_session(self, user_id, title=None, strategy='S3'):
        if strategy not in STRATEGIES:
            raise ValueError('unknown strategy')
        sid = 's_' + uuid.uuid4().hex[:16]
        self._exec('INSERT INTO sessions (id, user_id, title, created, strategy) VALUES (?,?,?,?,?)',
                   sid, user_id, title, time.time(), strategy)
        return self.session(sid)

    def set_strategy(self, sid, strategy):
        if strategy not in STRATEGIES:
            raise ValueError('unknown strategy')
        self._exec('UPDATE sessions SET strategy=? WHERE id=?', strategy, sid)

    def user_sessions(self, user_id, limit=30):
        """Conversations, newest activity first, with their first question and turn count (sidebar)."""
        sessions = self._all('SELECT id, title, strategy, created FROM sessions WHERE user_id=?', user_id)
        for s in sessions:
            runs = self._all('SELECT question, created FROM runs WHERE session_id=? ORDER BY created', s['id'])
            s.update(turns=len(runs), first_question=runs[0]['question'] if runs else None,
                     last_active=runs[-1]['created'] if runs else s['created'])
        return sorted(sessions, key=lambda s: -s['last_active'])[:limit]

    def session_turns(self, sid):
        """Finished turns of a conversation, oldest first: the next turn's context."""
        rows = self._all('SELECT question, standalone, status, final, evidence FROM runs WHERE session_id=? AND status IN '
                         f"({','.join('?' * len(TERMINAL))}) ORDER BY created", sid, *TERMINAL)
        return [{'utterance': r['question'], 'standalone': r['standalone'], 'status': r['status'],
                 'final': json.loads(r['final']) if r['final'] else None,
                 'evidence': json.loads(r['evidence']) if r['evidence'] else {}} for r in rows]

    def session_busy(self, sid):
        return self._one("SELECT id FROM runs WHERE session_id=? AND status IN ('queued','running') LIMIT 1", sid)

    # ---------- preferences (per user, never shared) ----------
    def preferences(self, user_id):
        return {r['key']: json.loads(r['value']) for r in self._all('SELECT key, value FROM preferences WHERE user_id=?', user_id)}

    def set_preferences(self, user_id, prefs):
        for k, v in prefs.items():
            if k == 'watchlist':
                if not isinstance(v, list) or len(v) > 20 or not all(isinstance(c, str) and 0 < len(c) <= 20 for c in v):
                    raise ValueError('watchlist must be up to 20 company names')
            elif k not in PREFERENCES:
                raise ValueError(f'unknown preference {k}')
            elif str(v) not in PREFERENCES[k]:
                raise ValueError(f'{k} must be one of {PREFERENCES[k]}')
        for k, v in prefs.items():
            self._exec('INSERT INTO preferences VALUES (?,?,?,?) ON CONFLICT(user_id,key) DO UPDATE SET '
                       'value=excluded.value, updated=excluded.updated', user_id, k,
                       json.dumps(v if k == 'watchlist' else str(v), ensure_ascii=False), time.time())
        return self.preferences(user_id)

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
        out = {**r, 'final': json.loads(r['final']) if r.get('final') else None}
        out.pop('evidence', None)   # cited chunk text is internal context for the next turn; the trajectory has it
        return out

    def set_status(self, rid, status, **fields):
        now = time.time()
        extra = {'started': now} if status == 'running' else {'finished': now} if status in TERMINAL else {}
        extra.update(fields)
        for k in ('final', 'evidence'):
            if k in extra:
                extra[k] = json.dumps(extra[k], ensure_ascii=False) if extra[k] is not None else None
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
