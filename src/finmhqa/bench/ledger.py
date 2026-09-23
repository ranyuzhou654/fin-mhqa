"""Source-verified fact ledger for benchmark_v1.

Facts are read from the three primary statements only. A fact is admitted when its
unit has an explicit source, its value is unambiguous, and it is confirmed either by an
accounting identity or by the same value in an independent table elsewhere in the
report. Nothing is repaired or guessed; every failure is kept with its reason.
"""
from collections import defaultdict
from decimal import Decimal, InvalidOperation
import re

FACTORS = {'元': Decimal(1), '千元': Decimal(1000), '万元': Decimal(10000), '百万元': Decimal(1000000), '亿元': Decimal(100000000)}

# metric id -> (statement, row names after normalisation, Chinese label)
METRICS = {
    'revenue': ('IS', {'营业收入'}, '营业收入'),
    'cost_of_revenue': ('IS', {'营业成本'}, '营业成本'),
    'selling_expense': ('IS', {'销售费用'}, '销售费用'),
    'admin_expense': ('IS', {'管理费用'}, '管理费用'),
    'rd_expense': ('IS', {'研发费用'}, '研发费用'),
    'net_profit': ('IS', {'净利润'}, '净利润'),
    'np_parent': ('IS', {'归属于母公司股东的净利润', '归属于母公司所有者的净利润'}, '归属于母公司股东的净利润'),
    # 比亚迪 labels a negative net flow '使用的现金流量净额'; it is the same line item.
    'cfo_net': ('CF', {'经营活动产生的现金流量净额', '经营活动使用的现金流量净额'}, '经营活动产生的现金流量净额'),
    'cfi_net': ('CF', {'投资活动产生的现金流量净额', '投资活动使用的现金流量净额'}, '投资活动产生的现金流量净额'),
    'total_assets': ('BS', {'资产总计'}, '资产总计'),
    'total_liabilities': ('BS', {'负债合计'}, '负债合计'),
    'equity_parent': ('BS', {'归属于母公司所有者权益合计', '归属于母公司股东权益合计'}, '归属于母公司所有者权益合计'),
    'cash': ('BS', {'货币资金'}, '货币资金'),
}
# Rows used only inside identities; never become questions.
HELPERS = {
    'minority_pl': ('IS', {'少数股东损益'}),
    'total_equity': ('BS', {'所有者权益合计', '股东权益合计'}),
    'minority_equity': ('BS', {'少数股东权益'}),
    'liab_equity_total': ('BS', {'负债和所有者权益总计', '负债和股东权益总计', '负债及所有者权益总计', '负债及股东权益总计'}),
    'total_profit': ('IS', {'利润总额'}),
    'income_tax': ('IS', {'所得税费用', '所得税'}),
    'cfo_in': ('CF', {'经营活动现金流入小计'}),
    'cfo_out': ('CF', {'经营活动现金流出小计'}),
    'cfi_in': ('CF', {'投资活动现金流入小计'}),
    'cfi_out': ('CF', {'投资活动现金流出小计'}),
}
# Extra row names that other report tables use for the same quantity (e.g. 第二节 主要会计数据).
CORROBORATION_NAMES = {
    'np_parent': {'归属于上市公司股东的净利润'},
    'total_assets': {'总资产'},
    'equity_parent': {'归属于上市公司股东的净资产', '归属于上市公司股东的所有者权益'},
}
# Expense lines some reports print in parentheses as deductions (美的); their economic value is the magnitude.
EXPENSES = ('cost_of_revenue', 'selling_expense', 'admin_expense', 'rd_expense')
PARENT_METRICS = ('revenue', 'net_profit', 'cfo_net', 'total_assets')
PERIODS = {'IS': ('2025H1', '2024H1'), 'CF': ('2025H1', '2024H1'), 'BS': ('2025-06-30', '2024-12-31')}
KIND = {'资产负债表': 'BS', '利润表': 'IS', '现金流量表': 'CF'}
TITLE = re.compile(r'(合并及公司|合并|母公司|公司)(资产负债表|利润表|现金流量表)\s*$')
UNIT_DECLARATION = re.compile(r'(单位|金额单位)\s*(为|是)?\s*[：:]?\s*(人民币)?\s*(百万元|千元|万元|亿元|元)')
CURRENT_MARK = re.compile(r'2025|本报告期|本期|期末')
PRIOR_MARK = re.compile(r'2024|上年|上期|期初')


def period_tag(label):
    # prior first: '上年期末数' contains 期末 but is the prior balance
    return 'prior' if PRIOR_MARK.search(label) else 'current' if CURRENT_MARK.search(label) else None


def normalise(name):
    name = re.sub(r'\s+', '', name)
    name = re.sub(r'[（(][^（）()]*[)）]', '', name)
    previous = None
    while previous != name:
        previous = name
        name = re.sub(r'^([一二三四五六七八九十]+、|[（(][一二三四五六七八九十\d]+[)）]|\d+[\.、．]|其中[：:]|减[：:]|加[：:])', '', name)
    return name.rstrip('：:')


def parse_number(cell):
    """Parse a statement cell. Returns (Decimal, raw string) or None for empty/dash cells."""
    raw = cell.strip()
    text = raw.replace(',', '').replace('，', '').replace(' ', '')
    if text in ('', '-', '－', '—', '--'):
        return None
    negative = text.startswith('(') and text.endswith(')') or text.startswith('（') and text.endswith('）')
    text = text.strip('()（）').replace('－', '-')
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    return (-value if negative else value), raw


def md_rows(chunk):
    rows = []
    for line in chunk['text'].split('\n'):
        line = line.strip()
        if not line.startswith('|') or re.fullmatch(r'\|[-|\s:]+\|', line):
            continue
        rows.append([c.strip() for c in line.strip('|').split('|')])
    return rows


def title_of(chunk):
    title = re.sub(r'^[\d一二三四五六七八九十]+[、.．]\s*', '', (chunk.get('table_title') or '').strip())
    title = re.sub(r'^20\d\d\s*年\s*半年度\s*', '', title)
    match = TITLE.search(title)
    if not match:
        return None
    scope = {'合并及公司': 'both', '合并': 'consolidated', '母公司': 'parent', '公司': 'parent'}[match.group(1)]
    return KIND[match.group(2)], scope


TEXT_HEADER = re.compile(r'(合并及公司|合并|母公司)(资产负债表|利润表|现金流量表)\s*\n?\s*20\d\d')
EQUITY_STATEMENT = re.compile(r'(所有者|股东)权益变动表')
# Rows that only occur in an equity-change statement; they end a statement whose title Docling lost.
EQUITY_ROWS = {'上年年末余额', '上年期末余额', '本年期初余额', '本年年初余额', '本期期初余额', '本期增减变动金额'}


def statement_segments(chunks):
    """Group one company's 第八节 table_md chunks into primary statements, in document order.

    A statement starts at a table titled with its name, or at a short text header naming it
    (some reports title the table generically). Unlabeled or generically titled tables continue
    the running statement; an equity-change statement or any other named table ends it."""
    ordered = sorted((c for c in chunks if c['chunk_type'] in ('table_md', 'text') and '财务报告' in c['section_path']),
                     key=lambda c: (c['page_start'], c['_order']))
    segments, current, pending = [], None, None
    for c in ordered:
        if c['chunk_type'] == 'text':
            body = c['text'].split('】', 1)[-1]
            if len(body) > 400:
                continue
            if EQUITY_STATEMENT.search(body):
                current, pending = None, None
            elif m := TEXT_HEADER.search(body):
                scope = {'合并及公司': 'both', '合并': 'consolidated', '母公司': 'parent'}[m.group(1)]
                pending, current = (KIND[m.group(2)], scope), None
            continue
        if any(normalise(r[0]) in EQUITY_ROWS for r in md_rows(c)):
            current, pending = None, None
            continue
        header = c['text'].split('\n')[1] if '\n' in c['text'] else ''
        generic = (c.get('table_title') or '') == c['section_path'].split('>')[-1].strip()
        kind = title_of(c) or (pending if pending and generic else None)
        pending = None
        if kind:
            if current is None or (current['kind'], current['scope']) != kind or c['page_start'] > current['pages'][-1] + 1:
                current = {'kind': kind[0], 'scope': kind[1], 'chunks': [], 'pages': []}
                segments.append(current)
        elif current is not None and ('单位：未标注' in header or generic) and c['page_start'] <= current['pages'][-1] + 1:
            pass  # unlabeled or generically titled continuation of the running statement
        else:
            current = None
            continue
        current['chunks'].append(c)
        if c['page_start'] not in current['pages']:
            current['pages'].append(c['page_start'])
    # a statement title appears once per statement; keep the first segment per (kind, scope)
    seen, result = set(), []
    for s in segments:
        if (s['kind'], s['scope']) not in seen:
            seen.add((s['kind'], s['scope']))
            result.append(s)
    return result


def layout(segment):
    """Value-column layout from the statement's first table header."""
    first = md_rows(segment['chunks'][0])
    header = first[0] if first else []
    numeric = sum(parse_number(c) is not None for c in header[1:])
    has_header = numeric == 0 and len(header) > 1
    note = has_header and len(header) > 1 and '附注' in header[1]
    values = header[1 + note:] if has_header else []
    period = 'column_order'
    if has_header and len(values) >= 2 and period_tag(values[0]) == 'current' and period_tag(values[1]) == 'prior':
        period = 'header'
    return {'ncols': len(header), 'note': note, 'has_header': has_header, 'period_source': period, 'header': header}


def company_unit_declaration(chunks):
    for c in sorted(chunks, key=lambda c: (c['page_start'], c['_order'])):
        if c['chunk_type'] == 'text' and '财务报告' in c['section_path']:
            body = c['text'].split('】', 1)[-1]
            if len(body) <= 200 and '编制单位' not in body and (m := UNIT_DECLARATION.search(body)):
                return m.group(4), c['chunk_id']
    return None, None


def extract_segment(segment, scope, fallback_unit):
    """Rows of one statement segment -> {row name: [(values..., chunk, unit, unit_source)]}."""
    lay = layout(segment)
    offset = 2 if scope == 'parent' and segment['scope'] == 'both' else 0
    segment_unit = next(((c['unit'], c['chunk_id']) for c in segment['chunks'] if c.get('unit')), None)
    found, problems = defaultdict(list), []
    for c in segment['chunks']:
        rows = md_rows(c)
        if not rows:
            continue
        note = lay['note']
        if len(rows[0]) != lay['ncols']:
            if lay['note'] and len(rows[0]) == lay['ncols'] - 1:
                note = False
            else:
                problems.append({'chunk_id': c['chunk_id'], 'problem': f"column count {len(rows[0])} differs from statement header {lay['ncols']}"})
                continue
        if c.get('unit'):
            unit = (c['unit'], c['chunk_id'], 'table_header')
        elif segment_unit:
            unit = (segment_unit[0], segment_unit[1], 'statement_header')
        elif fallback_unit[0]:
            unit = (fallback_unit[0], fallback_unit[1], 'report_declaration')
        else:
            unit = (None, None, 'missing')
        for row in rows:
            name = normalise(row[0])
            cells = row[1 + note:]
            if len(cells) < offset + 2:
                continue
            found[name].append({'current': parse_number(cells[offset]), 'prior': parse_number(cells[offset + 1]),
                                'chunk': c, 'unit': unit})
    return found, lay, problems


def digits(raw):
    return re.sub(r'[^\d.\-()（）]', '', raw)


def kv_corroboration(chunks, excluded_ids):
    """(normalised row name) -> [(value in yuan or None, raw digit string, period tag, chunk_id)] outside the statements.

    A table without a unit can still corroborate the exact digit string of a fact; the unit itself
    must then come from the statement header or a report-level declaration."""
    index = defaultdict(list)
    for c in chunks:
        if c['chunk_type'] != 'table_kv' or c['chunk_id'] in excluded_ids:
            continue
        parent = c.get('parent_table_id')
        if parent in excluded_ids:
            continue
        for line in c['text'].split('\n')[2:]:
            if '：' not in line:
                continue
            name, rest = line.split('：', 1)
            for pair in rest.split('，'):
                match = re.search(r'(.*?)\s*(\(?-?[\d,]+(?:\.\d+)?\)?)\s*$', pair)
                if not match:
                    continue
                parsed = parse_number(match.group(2))
                if parsed is None:
                    continue
                label = match.group(1)
                tag = period_tag(label)
                value = parsed[0] * FACTORS[c['unit']] if c.get('unit') in FACTORS else None
                index[normalise(name)].append((value, digits(match.group(2)), tag, c['chunk_id']))
    return index


def build_company(company, chunks):
    unit_decl = company_unit_declaration(chunks)
    segments = statement_segments(chunks)
    statement_ids = {c['chunk_id'] for s in segments for c in s['chunks']}
    statement_parents = {c.get('parent_table_id') for s in segments for c in s['chunks'] if c.get('parent_table_id')}
    kv_ids = {c['chunk_id'] for c in chunks if c['chunk_type'] == 'table_kv' and c.get('parent_table_id') in statement_parents}
    corroboration = kv_corroboration(chunks, statement_ids | kv_ids | statement_parents)
    records, notes = [], []
    for scope in ('consolidated', 'parent'):
        for kind in ('IS', 'CF', 'BS'):
            segment = next((s for s in segments if s['kind'] == kind and s['scope'] in (scope, 'both')), None)
            if segment is None:
                notes.append({'company': company, 'scope': scope, 'statement': kind, 'problem': 'statement not located'})
                continue
            rows, lay, problems = extract_segment(segment, scope, unit_decl)
            notes.extend({'company': company, 'scope': scope, 'statement': kind, **p} for p in problems)
            wanted = {k: v for k, v in METRICS.items() if v[0] == kind and (scope == 'consolidated' or k in PARENT_METRICS)}
            wanted.update({k: (v[0], v[1], None) for k, v in HELPERS.items() if v[0] == kind})
            for metric, (_, names, label) in wanted.items():
                hits = [h for n in names for h in rows.get(n, [])]
                for position, period in zip(('current', 'prior'), PERIODS[kind]):
                    records.append(make_record(company, scope, kind, metric, label, period, position, hits, lay, segment))
    return records, notes, corroboration, segments, unit_decl


def make_record(company, scope, kind, metric, label, period, position, hits, lay, segment):
    record = {'company': company, 'scope': scope, 'statement': kind, 'metric': metric, 'metric_label': label,
              'period': period, 'helper': label is None, 'period_source': lay['period_source'],
              'statement_pages': segment['pages'], 'problems': []}
    values = {}
    for h in hits:
        cell = h[position]
        if cell is not None:
            values.setdefault(cell[0], []).append((cell[1], h))
    if not values:
        record['problems'].append('row or cell not found')
        return record
    units = {h['unit'][0] for vs in values.values() for _, h in vs}
    if len(values) > 1:
        record['problems'].append(f'conflicting values {sorted(str(v) for v in values)}')
        return record
    value, occurrences = next(iter(values.items()))
    raw, hit = occurrences[0]
    if len(units) != 1 or None in units:
        record['problems'].append('unit missing or inconsistent across occurrences')
    c = hit['chunk']
    if raw not in c['text']:
        record['problems'].append('raw value string not found verbatim in chunk')
    unit, unit_source, unit_kind = hit['unit']
    if metric in EXPENSES and value < 0 and raw.lstrip().startswith(('(', '（')):
        value = -value
        record['sign_note'] = 'expense printed in parentheses as a deduction; magnitude recorded'
    record.update(value=str(value), raw_value=raw, unit=unit, unit_source_chunk=unit_source, unit_source_kind=unit_kind,
                  value_yuan=str(value * FACTORS[unit]) if unit in FACTORS else None,
                  chunk_id=c['chunk_id'], page=c['page_start'], source_sha256=c['source_sha256'],
                  company_code=c['company_code'], occurrences=len(occurrences))
    return record


def last_digit(record):
    """One unit of the last printed digit, in yuan: rounded source cells may differ by this much."""
    raw = record['raw_value'].replace(',', '')
    decimals = len(raw.split('.')[1].rstrip(')）')) if '.' in raw else 0
    return FACTORS[record['unit']] * Decimal(1).scaleb(-decimals)


def check_identities(records):
    """Mark identity results on facts; each side may differ by one printed last digit per term."""
    table = {(r['company'], r['scope'], r['metric'], r['period']): Decimal(r['value_yuan'])
             for r in records if r.get('value_yuan') and not r['problems']}
    tolerance = {(r['company'], r['scope'], r['period']): last_digit(r) for r in records if r.get('value_yuan') and not r['problems']}
    def get(r, metric):
        return table.get((r['company'], r['scope'], metric, r['period']))
    for r in records:
        r['identity'] = None
        if r['problems'] or not r.get('value_yuan'):
            continue
        m = r['metric']
        checks = []
        tol = tolerance[(r['company'], r['scope'], r['period'])]
        close = lambda x, y, terms=2: abs(x - y) <= tol * terms
        if m in ('total_assets', 'total_liabilities'):
            a, l, e, t = get(r, 'total_assets'), get(r, 'total_liabilities'), get(r, 'total_equity'), get(r, 'liab_equity_total')
            if None not in (a, l, e):
                checks.append(('资产总计 = 负债合计 + 所有者权益合计', close(a, l + e)))
            if a is not None and t is not None:
                checks.append(('资产总计 = 负债和所有者权益总计', a == t))
        if m == 'equity_parent':
            e, p, mi = get(r, 'total_equity'), get(r, 'equity_parent'), get(r, 'minority_equity')
            if e is not None and p is not None:
                checks.append(('所有者权益合计 = 归母权益 + 少数股东权益', close(e, p + (mi or 0))))
        if m in ('net_profit', 'np_parent') and r['scope'] == 'consolidated':
            n, p, mi = get(r, 'net_profit'), get(r, 'np_parent'), get(r, 'minority_pl')
            if None not in (n, p, mi):
                checks.append(('净利润 = 归母净利润 + 少数股东损益', close(n, p + mi)))
        if m == 'net_profit':
            n, t, x = get(r, 'net_profit'), get(r, 'total_profit'), get(r, 'income_tax')
            if None not in (n, t, x):
                # tax may be a benefit (negative, n = t - x) or printed in parentheses as a deduction (n = t + x)
                checks.append(('净利润 = 利润总额 − 所得税费用', close(n, t - x) or close(n, t + x)))
        for net, inflow, outflow, label in (('cfo_net', 'cfo_in', 'cfo_out', '经营'), ('cfi_net', 'cfi_in', 'cfi_out', '投资')):
            if m == net:
                n, i, o = get(r, net), get(r, inflow), get(r, outflow)
                if None not in (n, i, o):
                    # outflow subtotals are shown positive in most reports and in parentheses in some
                    checks.append((f'{label}净额 = 流入小计 − 流出小计', close(n, i - abs(o))))
        if checks:
            r['identity'] = {'checks': [{'rule': rule, 'passed': ok} for rule, ok in checks], 'passed': all(ok for _, ok in checks)}


def check_corroboration(records, corroboration_by_company):
    for r in records:
        r['corroboration'] = []
        if r['problems'] or not r.get('value_yuan') or r['helper']:
            continue
        names = METRICS[r['metric']][1] | CORROBORATION_NAMES.get(r['metric'], set())
        target = Decimal(r['value_yuan'])
        position = 'current' if r['period'] in ('2025H1', '2025-06-30') else 'prior'
        raw = digits(r['raw_value'])
        if r.get('sign_note'):
            raw = raw.strip('()（）')
        for name in names:
            for value, text, tag, chunk_id in corroboration_by_company[r['company']].get(name, []):
                # yuan equality when the other table has a unit; else the exact digit string, which must
                # carry enough precision (decimals or >= 7 digits) not to match by coincidence
                if r.get('sign_note'):
                    value, text = (abs(value) if value is not None else None), text.strip('()（）').lstrip('-')
                by_value = value is not None and value == target
                by_string = value is None and text == raw and ('.' in raw or len(raw.replace('-', '')) >= 7)
                if by_value or by_string:
                    r['corroboration'].append({'chunk_id': chunk_id, 'row': name, 'period_tag': tag, 'match': 'value' if by_value else 'digits',
                                               'period_matches': tag == position if tag else None})


def admit(record):
    """Admission decision; returns list of reasons for rejection (empty = admitted)."""
    reasons = list(record['problems'])
    if reasons:
        return reasons
    if record['unit'] not in FACTORS:
        reasons.append('unit has no explicit source')
    identity_ok = bool(record['identity'] and record['identity']['passed'])
    if record['identity'] and not record['identity']['passed']:
        reasons.append('accounting identity failed')
    corroborated = bool(record['corroboration'])
    if not identity_ok and not corroborated:
        reasons.append('neither identity nor independent table confirms the value')
    period_confirmed = record['period_source'] == 'header' or any(c['period_matches'] for c in record['corroboration'])
    if any(c['period_matches'] is False for c in record['corroboration']) and not any(c['period_matches'] for c in record['corroboration']):
        reasons.append('independent table shows this value under the other period')
    if not period_confirmed:
        reasons.append('period only inferred from column order')
    return reasons
