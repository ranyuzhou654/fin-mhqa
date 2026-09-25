"""Blind fault injection v1 (docs/FAULT_INJECTION_V1_PLAN.md).

A plan per (Dev item, fault) is precomputed from gold, ledger and the admitted corpus: which chunks
change and how, and the answer an agent would give if it trusted the corrupted evidence ("misled").
At run time a Transformer rewrites retrieval and context responses deterministically; every raw
response is logged so an audit can replay raw -> transform byte for byte. Nothing here adds a hint.
"""
from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
import re
from finmhqa.agent.pilot_tools import FACTORS
from finmhqa.bench.generate import (Ledger, RATIOS, STATEMENT, amount_answer, percent_answer, pct, ratio_value)

FAULTS = ('F1', 'F2', 'F3')
OTHER_PERIOD = {'2025H1': '2024H1', '2024H1': '2025H1', '2025-06-30': '2024-12-31', '2024-12-31': '2025-06-30'}
CURRENT_PERIODS = ('2025H1', '2025-06-30')
UNIT_SWAP = {'元': '千元', '千元': '元'}
TITLE_UNIT = re.compile(r'(　单位：)(未标注|元|千元|万元|百万元|亿元)')
F3_FACTOR = Decimal('1.07')
SUMMARY_SECTION = '主要会计数据'


def token(raw):
    """A number cell exactly: not part of a longer number."""
    return re.compile(r'(?<![\d.,])' + re.escape(raw) + r'(?![\d.,])')


def format_like(raw, value):
    """Write value with raw's decimals, thousands separators and negative style."""
    digits = raw.strip('()').lstrip('-')
    decimals = len(digits.split('.')[1]) if '.' in digits else 0
    q = abs(value).quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)
    text = f'{q:,.{decimals}f}'
    if value < 0:
        text = f'({text})' if raw.startswith('(') else '-' + text
    return text, (q if value >= 0 else -q)


# ---------- misled answers: re-evaluate the frozen derivation with corrupted fact values ----------

def evaluate(derivation, facts, ledger):
    """(answer dict, selected company) for a gold derivation over (possibly corrupted) facts; None if undefined."""
    y = lambda f: Decimal(f['value_yuan'])
    selected = None
    m = re.fullmatch(r'select_(max|min)\(([^)]*)\)->(.+)', derivation)
    if m:
        pool = [facts[i] for i in m.group(2).split(',')]
        chosen = (max if m.group(1) == 'max' else min)(pool, key=y)
        if sum(y(f) == y(chosen) for f in pool) > 1:
            return None
        selected, derivation = chosen['company'], m.group(3)
        if derivation in RATIOS:
            r = ratio_value(ledger, selected, derivation)
            return (percent_answer(r[0]), selected) if r else None

    def fact(i):
        f = facts[i]
        if selected is None or f['company'] == selected:
            return f
        return ledger.get(selected, f['metric'], f['period'], f['scope'])

    m = re.fullmatch(r'(\w+)\((.*)\)', derivation)
    op, args = m.group(1), m.group(2)
    if op == 'ratio':
        head, rest = args.split(',', 1)
        parts = re.fullmatch(r'sum\((.*)\)', rest).group(1).split(',')
        total = sum(y(fact(i)) for i in parts)
        return (percent_answer(pct(y(fact(head)) / total)), selected) if total else None
    vals = [fact(i) for i in args.split(',')]
    if any(v is None for v in vals):
        return None
    vals = [y(v) for v in vals]
    if op == 'convert':
        return amount_answer(vals[0]), selected
    if op == 'sum':
        return amount_answer(sum(vals)), selected
    if op == 'difference':
        return amount_answer(vals[0] - vals[1]), selected
    if op == 'growth':
        return (percent_answer(pct((vals[0] - vals[1]) / vals[1])), selected) if vals[1] > 0 else None
    raise ValueError(f'unknown derivation {derivation}')


def answer_under(item, admitted, overrides):
    facts = {i: dict(f, value_yuan=overrides.get(i, f['value_yuan'])) for i, f in admitted.items()}
    ledger = Ledger(list(facts.values()))
    facts = {f['fact_id']: f for f in ledger.by_key.values()}
    out = evaluate(item['derivation'], facts, ledger)
    if out is None:
        return None
    answer, selected = out
    return {'value': answer['value'], 'unit': answer['unit'],
            'selected_company': selected if item['answer']['selected_company'] is not None else None}


# ---------- plans ----------

def synthetic_id(item_id, kind):
    return hashlib.sha256(f'fault_v1|{item_id}|{kind}'.encode()).hexdigest()[:24]


def build_plan(item, fault, chunks, admitted, reviewed):
    """Deterministic plan for one answerable item; raises if the fault cannot be injected blindly."""
    target = admitted[item['facts'][-1]]
    anchor = chunks[target['chunk_id']]
    group = sorted(c['chunk_id'] for c in chunks.values()
                   if anchor.get('parent_table_id') and c.get('parent_table_id') == anchor['parent_table_id'])
    plan = {'id': item['id'], 'fault': fault, 'target_fact': target['fact_id'], 'company': target['company'],
            'source_sha256': target['source_sha256'], 'truth': {k: item['answer'][k] for k in ('value', 'unit', 'selected_company')}}
    overrides = {}
    if fault == 'F1':
        if target['unit'] not in UNIT_SWAP:
            raise ValueError('F1 needs 元 or 千元')
        wrong = UNIT_SWAP[target['unit']]
        for cid in group:
            if not TITLE_UNIT.search(chunks[cid]['text']):
                raise ValueError(f'F1: no title unit in {cid}')
        scale = FACTORS[wrong] / FACTORS[target['unit']]
        for f in admitted.values():
            if f['chunk_id'] in group:
                if f['unit'] != target['unit']:
                    raise ValueError('F1: mixed units inside one table')
                overrides[f['fact_id']] = str(Decimal(f['value']) * FACTORS[wrong])
        plan.update(chunks=group, true_unit=target['unit'], wrong_unit=wrong, scale=str(scale))
    elif fault == 'F2':
        pool = list(admitted.values()) + [f for f in reviewed if f.get('raw_value')]
        pair = [f for f in pool if f['chunk_id'] == target['chunk_id'] and f['company'] == target['company']
                and f['scope'] == target['scope'] and f['metric'] == target['metric'] and f['period'] == OTHER_PERIOD[target['period']]]
        if len(pair) != 1 or pair[0]['raw_value'] == target['raw_value']:
            raise ValueError('F2: no distinct counterpart value in the same table')
        other = pair[0]
        lines = 0
        for cid in group:
            for line in chunks[cid]['text'].split('\n'):
                a, b = len(token(target['raw_value']).findall(line)), len(token(other['raw_value']).findall(line))
                if a and b:   # only rows carrying both periods are swapped; a lone equal number elsewhere stays
                    if (a, b) != (1, 1):
                        raise ValueError(f'F2: values not paired once in a row of {cid}')
                    lines += 1
        if not lines:
            raise ValueError('F2: target row not found')
        overrides[target['fact_id']] = other['value_yuan']
        if other['fact_id'] in admitted:
            overrides[other['fact_id']] = target['value_yuan']
        plan.update(chunks=group, swap=[target['raw_value'], other['raw_value']], counterpart_fact=other['fact_id'], rows=lines)
    elif fault == 'F3':
        doc = [c for c in chunks.values() if c['company'] == target['company'] and c['source_sha256'] == target['source_sha256']]
        home = sorted((c for c in doc if SUMMARY_SECTION in (c.get('section_path') or '') and c['chunk_type'] == 'table_md'),
                      key=lambda c: (c['page_start'], c.get('part', 0), c['chunk_id']))
        if not home:
            raise ValueError('F3: no summary section in the report')
        home = home[0]
        text, fake = format_like(target['raw_value'], Decimal(target['value']) * F3_FACTOR)
        pool = list(admitted.values()) + [f for f in reviewed if f.get('raw_value')]
        pair = [f for f in pool if f['chunk_id'] == target['chunk_id'] and f['company'] == target['company'] and f['scope'] == target['scope']
                and f['metric'] == target['metric'] and f['period'] == OTHER_PERIOD[target['period']]]
        balance = STATEMENT[target['metric']] == 'BS'
        header = ('本报告期末', '上年度末') if balance else ('本报告期', '上年同期')
        cells = {target['period']: text}
        if len(pair) == 1:
            cells[pair[0]['period']] = pair[0]['raw_value']
        current = target['period'] in CURRENT_PERIODS
        order = [(header[0], target['period'] if current else OTHER_PERIOD[target['period']]),
                 (header[1], OTHER_PERIOD[target['period']] if current else target['period'])]
        order = [(h, p) for h, p in order if p in cells]
        title = ('母公司' if target['scope'] == 'parent' else '') + '主要会计数据摘要'
        prefix = home['text'].split('】', 1)[0] + '】'
        body = (f"{prefix}\n表：{title}　单位：{target['unit']}　币种：人民币\n"
                f"| 项目 | {' | '.join(h for h, _ in order)} |\n|{'---|' * (len(order) + 1)}\n"
                f"| {target['metric_label']} | {' | '.join(cells[p] for _, p in order)} |")
        sid = synthetic_id(item['id'], 'chunk')
        if sid in chunks:
            raise ValueError('F3: synthetic id collides with the corpus')
        synthetic = {k: deepcopy(v) for k, v in home.items()}
        synthetic.update(chunk_type='table_md', text=body, table_title=title, unit=target['unit'], currency='人民币',
                         parent_table_id=synthetic_id(item['id'], 'table'), part=0, unit_source='table_local_explicit',
                         n_tokens=max(1, len(body) // 2), page_start=home['page_start'], page_end=home['page_start'], chunk_id=sid)
        overrides[target['fact_id']] = str(fake * FACTORS[target['unit']])
        plan.update(home_chunk=home['chunk_id'], synthetic=synthetic, wrong_raw=text)
    else:
        raise ValueError('unknown fault')
    misled = answer_under(item, admitted, overrides)
    plan.update(overrides=overrides, misled=misled,
                effective=misled is None or any(misled[k] != plan['truth'][k] for k in ('value', 'unit', 'selected_company')),
                outside_corroboration=sum(c['chunk_id'] not in set(plan.get('chunks', [])) for c in target['corroboration']))
    return plan


# ---------- runtime transformation (pure given plan + state; replayed by the audit) ----------

class Transformer:
    def __init__(self, plan, by_id):
        self.plan, self.by_id, self.injected = plan, by_id, False
        self.group = set(plan.get('chunks', []))
        self.sid = plan['synthetic']['chunk_id'] if plan['fault'] == 'F3' else None

    def chunk(self, c):
        if c['chunk_id'] not in self.group:
            return c
        c = deepcopy(c)
        if self.plan['fault'] == 'F1':
            c['text'] = TITLE_UNIT.sub(lambda m: m.group(1) + self.plan['wrong_unit'], c['text'], count=1)
            c['unit'] = self.plan['wrong_unit']
            c['unit_source'] = 'table_local_explicit'
        else:
            a, b = self.plan['swap']
            lines = []
            for line in c['text'].split('\n'):
                if token(a).search(line) and token(b).search(line):
                    line = token(b).sub(lambda _: '\0', token(a).sub(lambda _: '\1', line)).replace('\1', b).replace('\0', a)
                lines.append(line)
            c['text'] = '\n'.join(lines)
        return c

    def context_args(self, args):
        """The real read to perform; None when the synthetic anchor answers by itself."""
        if args.get('anchor_id') != self.sid:
            return args
        if args['scope'] in ('table', 'table_continuation'):
            return None
        return dict(args, anchor_id=self.plan['home_chunk'])

    def search(self, args, raw):
        out = deepcopy(raw)
        out['results'] = [self.chunk(c) for c in raw['results']]
        note = {'changed': [c['chunk_id'] for c in raw['results'] if c['chunk_id'] in self.group]}
        if self.sid and not self.injected and args.get('company') == self.plan['company']:
            first = raw['results'][0] if raw['results'] else {}
            synthetic = self.plan['synthetic']
            row = {k: synthetic.get(k, first[k]) for k in first}
            row.update({k: v for k, v in synthetic.items() if k not in row})
            if 'section' in first:
                row['section'] = synthetic['section_path']
            row['attached'] = False
            out['results'].insert(0, row)
            self.injected = True
            note['inserted'] = synthetic['chunk_id']
        return out, note

    def _page_of(self, args):
        anchor = self.by_id.get(args['anchor_id']) if args['anchor_id'] != self.sid else self.plan['synthetic']
        scope = args['scope']
        if anchor is None or scope not in ('page', 'previous_page', 'next_page'):
            return None
        if (anchor['company'], anchor['source_sha256']) != (self.plan['company'], self.plan['source_sha256']):
            return None
        return anchor['page_start'] if scope == 'page' else anchor['page_start'] - 1 if scope == 'previous_page' else anchor['page_end'] + 1

    def read(self, args, raw):
        synthetic = self.plan.get('synthetic')
        if raw is None:   # table / table_continuation anchored on the synthetic chunk
            if args['cursor'] > (1 if args['scope'] == 'table' else 0):
                raise ValueError('context cursor beyond candidates')
            results = [deepcopy(synthetic)] if args['scope'] == 'table' and args['cursor'] == 0 else []
            extra = {'continuation_candidates': 0} if args['scope'] == 'table' else {
                'relation_note': 'unlabeled table on the next page in the same section; continuation is not proven'}
            total = 1 if args['scope'] == 'table' else 0
            return {**extra, 'results': results, 'anchor_id': args['anchor_id'], 'scope': args['scope'],
                    'dataset_sha256': self.plan['dataset_sha256'], 'index_admission': 'ACCEPTED', 'total_candidates': total,
                    'next_cursor': None, 'text_chars': sum(len(c['text']) for c in results),
                    'coverage': 'admitted chunks only, not complete PDF page',
                    'relation': 'same document; adjacency does not prove same table or scope'}, {'synthetic_anchor': True}
        out = deepcopy(raw)
        out['results'] = [self.chunk(c) for c in raw['results']]
        note = {'changed': [c['chunk_id'] for c in raw['results'] if c['chunk_id'] in self.group]}
        if args['anchor_id'] == self.sid:
            out['anchor_id'] = self.sid
            note['synthetic_anchor'] = True
        if synthetic and self.injected and args['cursor'] == 0 and self._page_of(args) == synthetic['page_start']:
            out['results'].append(deepcopy(synthetic))
            out['total_candidates'] += 1
            out['text_chars'] += len(synthetic['text'])
            note['inserted'] = synthetic['chunk_id']
        return out, note


def sha(obj):
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


class FaultInjector:
    """Wraps the real search and context reader; logs raw, transform note and output digest per call."""

    def __init__(self, plan, by_id, search, read, log_path):
        self.t, self.search_fn, self.read_fn, self.log_path, self.seq = Transformer(plan, by_id), search, read, log_path, 0

    def _log(self, tool, args, raw_args, raw, note, out):
        with self.log_path.open('a') as f:
            f.write(json.dumps({'seq': self.seq, 'tool': tool, 'args': args, 'raw_args': raw_args, 'raw': raw,
                                'note': note, 'out_sha256': sha(out)}, ensure_ascii=False) + '\n')
        self.seq += 1

    def search(self, args):
        raw = self.search_fn(args)
        out, note = self.t.search(args, raw)
        self._log('search', args, args, raw, note, out)
        return out

    def read(self, args):
        raw_args = self.t.context_args(args)
        raw = self.read_fn(raw_args) if raw_args is not None else None
        out, note = self.t.read(args, raw)
        self._log('read_evidence_context', args, raw_args, raw, note, out)
        return out


class FaultReplay:
    """Audit side: consume the fault log in order and prove each observed response = transform(raw)."""

    def __init__(self, plan, context, log_path):
        self.t, self.context = Transformer(plan, context.by_id), context
        self.log = [json.loads(l) for l in log_path.read_text().splitlines()] if log_path.exists() else []
        self.pos = 0

    def check(self, tool, args, response):
        if self.pos >= len(self.log):
            raise ValueError('fault log shorter than trajectory')
        e = self.log[self.pos]
        self.pos += 1
        if e['tool'] != tool or e['args'] != args:
            raise ValueError('fault log differs from tool request')
        if tool == 'search':
            out, note = self.t.search(args, e['raw'])
        else:
            if e['raw_args'] != self.t.context_args(args):
                raise ValueError('fault raw read args mismatch')
            if e['raw_args'] is not None and self.context.read(e['raw_args']) != e['raw']:
                raise ValueError('raw context replay mismatch')
            out, note = self.t.read(args, e['raw'])
        if out != response or note != e['note'] or sha(out) != e['out_sha256']:
            raise ValueError('fault transform replay mismatch')

    def finish(self):
        if self.pos != len(self.log):
            raise ValueError('fault log has unobserved entries')
        return {'fault_calls': len(self.log), 'inserted': any('inserted' in e['note'] for e in self.log),
                'changed_chunks_seen': sorted({c for e in self.log for c in e['note'].get('changed', [])})}
