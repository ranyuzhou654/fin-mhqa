"""Document-structure memory: where a company's metric rows sit in its report (docs/experiments/MEMORY_V1_PLAN.md).

What it stores is location only (company, report hash, chunk, page, scope, row label), never a value:
- written only from the citations of answers that passed the verifier and were published;
- a cited chunk must exist in the admitted index, and its row labels are read from the index text, not from what the
  run observed, so a tampered or injected tool response cannot put content into memory;
- reports are public, so this memory is shared by all users; per-user memory lives in the service store.

At question time the memory proposes chunks whose rows match the companies, metrics and scope named in the question.
The runner re-reads them from the index and gives them to the agent as already-observed hints; the agent still has
to check period, scope and unit, and every citation still goes through the usual publication guard.
"""
import re
import sqlite3
import threading

SCHEMA = '''CREATE TABLE IF NOT EXISTS doc_rows (
    chunk_id TEXT NOT NULL, row_label TEXT NOT NULL, company TEXT NOT NULL, source_sha256 TEXT NOT NULL,
    parent_table_id TEXT, chunk_type TEXT NOT NULL, page INTEGER, scope TEXT NOT NULL,
    uses INTEGER NOT NULL DEFAULT 1, first_run TEXT NOT NULL, last_run TEXT NOT NULL,
    PRIMARY KEY (chunk_id, row_label));
CREATE INDEX IF NOT EXISTS doc_rows_company ON doc_rows (company, row_label);'''
PREFIX = re.compile(r'^(?:[一二三四五六七八九十]+、|\d+[.、]|[（(][一二三四五六七八九十\d]+[）)]|其中[:：]|加[:：]|减[:：])+')
NOISE = re.compile(r'[\s　]|[（(][^）)]*[）)]$')


def normalize(label):
    return NOISE.sub('', PREFIX.sub('', label.strip()))


def row_labels(chunk):
    """First-column labels of a Markdown table chunk, or the labels before '：' of a key-value chunk."""
    labels = []
    for line in chunk['text'].splitlines():
        if chunk['chunk_type'] == 'table_md' and line.startswith('|') and not line.startswith('|---'):
            labels.append(line.strip('|').split('|')[0])
        elif chunk['chunk_type'] == 'table_kv' and '：' in line and not line.startswith('表：'):
            labels.append(line.split('：', 1)[0])
    out = []
    for label in map(normalize, labels):
        if 2 <= len(label) <= 30 and not re.fullmatch(r'[\d,.\-—%]+', label) and label not in out:
            out.append(label)
    return out[1:] if chunk['chunk_type'] == 'table_md' else out   # the md header row ('项目') is not a metric


def scope_of(chunk):
    where = f"{chunk.get('table_title') or ''} {chunk.get('section_path') or ''}"
    if '合并及公司' in where or '合并及母公司' in where:
        return 'both'
    return 'parent' if ('母公司' in where or '公司资产负债表' in where and '合并' not in where) else 'consolidated'


class DocMemory:
    def __init__(self, path=':memory:'):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.executescript(SCHEMA)
        self.lock = threading.Lock()

    def learn(self, final, run_id, index_by_id):
        """Record the rows of chunks cited by a published, verified answer. Returns what was written or refused."""
        report = {'written': [], 'refused': {}}
        if not final or final.get('status') != 'answered':
            report['refused']['*'] = 'no published answer'
            return report
        with self.lock, self.db:
            for cid in final.get('citations', []):
                chunk = index_by_id.get(cid)
                if chunk is None:
                    report['refused'][cid] = 'not in the admitted index'
                    continue
                if chunk['chunk_type'] not in ('table_md', 'table_kv'):
                    report['refused'][cid] = 'not a table chunk'
                    continue
                for label in row_labels(chunk):
                    self.db.execute('''INSERT INTO doc_rows VALUES (?,?,?,?,?,?,?,?,1,?,?)
                        ON CONFLICT(chunk_id,row_label) DO UPDATE SET uses=uses+1, last_run=excluded.last_run''',
                                    (cid, label, chunk['company'], chunk['source_sha256'], chunk.get('parent_table_id'),
                                     chunk['chunk_type'], chunk.get('page_start'), scope_of(chunk), run_id, run_id))
                report['written'].append(cid)
        return report

    def hints(self, question, companies, limit=6):
        """For each (company, metric) named in the question, the best remembered chunk: Markdown table before
        key-value, most-used first. Companies are interleaved so a multi-company question gets hints for each."""
        named = [c for c in companies if c in question]
        want = 'parent' if '母公司' in question else 'consolidated'
        per_company = []
        with self.lock:
            for company in named:
                rows = self.db.execute('SELECT chunk_id, row_label, scope, chunk_type, uses FROM doc_rows WHERE company=?',
                                       (company,)).fetchall()
                best = {}
                for cid, label, scope, kind, uses in rows:
                    if label in question and scope in (want, 'both'):
                        rank = (kind != 'table_md', -uses, cid)
                        if label not in best or rank < best[label][0]:
                            best[label] = (rank, cid)
                # a longer label wins over one it contains (营业总收入 vs 营业收入 when the question says 营业收入 only)
                labels = [l for l in best if not any(l != o and l in o and o in question for o in best)]
                per_company.append([best[l][1] for l in sorted(labels, key=lambda l: (-len(l), l))])
        out = []
        for i in range(max(map(len, per_company), default=0)):
            for chunks in per_company:
                if i < len(chunks) and chunks[i] not in out:
                    out.append(chunks[i])
        return out[:limit]

    def size(self):
        return self.db.execute('SELECT COUNT(DISTINCT chunk_id), COUNT(*) FROM doc_rows').fetchone()
