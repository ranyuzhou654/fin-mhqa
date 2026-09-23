"""Bounded reads of unchanged admitted chunks; no PDF parsing or inferred headers."""
from collections import defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

# Explicit report-wide declaration such as "财务报表及附注的单位为：千元"; ranking only, never applied automatically.
DECLARATION = re.compile(r'(单位|金额单位)\s*(为|是)?\s*[：:]?\s*(人民币)?\s*(元|千元|万元|百万元|亿元)')
UNLABELED = '单位：未标注'


def body(c):
    return c['text'].split('】', 1)[-1].strip()


def unit_rank(c, anchor):
    text = body(c)
    strong = bool(DECLARATION.search(text)) and len(text) <= 200
    report = '财务报告' in (c.get('section_path') or '') or '财务报告' in c['text'][:80]
    return (0 if strong and report else 1 if strong else 2, abs(c['page_start'] - anchor['page_start']))


class EvidenceContext:
    PAGE_SIZE = 6
    MAX_CHARS = 24000

    def __init__(self, index_dir, expected_dataset, version=2):
        # version 1 replays archived M3.1 runs: page-order unit candidates and no table continuation.
        if version not in (1, 2):
            raise ValueError('unknown context version')
        self.version = version
        root = Path(index_dir)
        manifest = json.loads((root / 'manifest.json').read_text())
        data = (root / 'chunk_meta.jsonl').read_bytes()
        if (manifest['admission']['status'] != 'ACCEPTED'
                or manifest['admission']['dataset_sha256'] != expected_dataset
                or hashlib.sha256(data).hexdigest() != expected_dataset):
            raise ValueError('context dataset/admission changed')
        self.dataset = expected_dataset
        chunks = [json.loads(line) for line in data.splitlines()]
        self.by_id = {c['chunk_id']: c for c in chunks}
        if len(chunks) != manifest['count'] or len(self.by_id) != len(chunks):
            raise ValueError('context count/identity mismatch')
        self.documents = defaultdict(list)
        for c in chunks:
            self.documents[self.document(c)].append(c)

    @staticmethod
    def document(c):
        return c['company'], c['source_sha256'], c['report_period']

    def read(self, args):
        if set(args) != {'anchor_id', 'scope', 'cursor'}:
            raise ValueError('invalid context fields')
        anchor = self.by_id.get(args['anchor_id'])
        if anchor is None:
            raise ValueError('unknown context anchor')
        cursor = args['cursor']
        if type(cursor) is not int or cursor < 0:
            raise ValueError('invalid context cursor')
        scope = args['scope']
        candidates = self.documents[self.document(anchor)]
        if scope == 'table':
            parent = anchor.get('parent_table_id')
            candidates = [c for c in candidates if parent and c.get('parent_table_id') == parent and c['chunk_type'] == 'table_md']
        elif scope == 'table_continuation' and self.version >= 2:
            # Docling does not merge a table split across pages when the next header is missing.
            # Offer unlabeled table parts on the following page in the same section as candidates only.
            parent = anchor.get('parent_table_id')
            if not parent:
                raise ValueError('table continuation requires a table anchor')
            parts = [c for c in candidates if c.get('parent_table_id') == parent]
            last = max(c['page_end'] for c in parts)
            candidates = [c for c in candidates if c['chunk_type'] == 'table_md' and c['page_start'] == last + 1
                          and c.get('parent_table_id') != parent and UNLABELED in c['text'][:200]
                          and c.get('section_path') == anchor.get('section_path')]
        elif scope in ('page', 'previous_page', 'next_page'):
            page = anchor['page_start'] if scope == 'page' else anchor['page_start'] - 1 if scope == 'previous_page' else anchor['page_end'] + 1
            candidates = [c for c in candidates if c['page_start'] <= page <= c['page_end'] and c['chunk_type'] != 'table_kv']
        elif scope == 'document_units':
            # Candidate declarations only. Their applicability to the target table
            # must still be checked by the model; local explicit units take precedence.
            candidates = [c for c in candidates if c['chunk_type'] == 'text'
                          and re.search(r'(单位|记账本位币)', c['text'])
                          and re.search(r'(人民币|千元|万元|亿元|元)', c['text'])]
        else:
            raise ValueError('invalid context scope')
        rank = (lambda c: unit_rank(c, anchor)) if scope == 'document_units' and self.version >= 2 else (lambda c: ())
        candidates = sorted(candidates, key=lambda c: (*rank(c), c['page_start'], c.get('part', 0), c['chunk_id']))
        if cursor > len(candidates):
            raise ValueError('context cursor beyond candidates')
        selected = []
        size = 0
        for c in candidates[cursor:cursor + self.PAGE_SIZE]:
            length = len(c['text'])
            if size + length > self.MAX_CHARS:
                if not selected:
                    raise ValueError('context chunk exceeds character bound; no text truncated')
                break
            selected.append(deepcopy(c))
            size += length
        end = cursor + len(selected)
        extra = {}
        if scope == 'table' and self.version >= 2:
            extra['continuation_candidates'] = len(self.read_continuation(anchor)) if anchor.get('parent_table_id') else 0
        if scope == 'table_continuation':
            extra['relation_note'] = 'unlabeled table on the next page in the same section; continuation is not proven'
        return {**extra, 'results': selected, 'anchor_id': args['anchor_id'], 'scope': scope,
                'dataset_sha256': self.dataset, 'index_admission': 'ACCEPTED',
                'total_candidates': len(candidates), 'next_cursor': end if end < len(candidates) else None,
                'text_chars': size, 'coverage': 'admitted chunks only, not complete PDF page',
                'relation': 'same document; adjacency does not prove same table or scope'}

    def read_continuation(self, anchor):
        return self.read(dict(anchor_id=anchor['chunk_id'], scope='table_continuation', cursor=0))['results']
