"""M1 pipeline. Production token counts require an explicitly supplied local tokenizer."""
import argparse
import hashlib
import json
from pathlib import Path
import re
from .structure import merge_cross_page_tables, walk_sections


def stable_id(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:24]


def split_bounded(text, count, limit=450):
    if limit < 1:
        raise ValueError('limit must be positive')
    while text:
        if count(text) <= limit:
            yield text
            return
        end = min(len(text), limit)
        while end > 0 and count(text[:end]) > limit:
            end //= 2
        if not end:
            raise ValueError('A single character exceeds token limit')
        boundary = max((m.end() for m in re.finditer(r'[。；！？\n]', text[:end])), default=end)
        yield text[:boundary]
        text = text[boundary:]


def to_md(rows):
    escape = lambda c: c.replace('|', '\\|').replace('\n', '<br>')
    line = lambda row: '| ' + ' | '.join(map(escape, row)) + ' |'
    return '\n'.join([line(rows[0]), '|' + '---|' * len(rows[0]), *map(line, rows[1:])])


def normalize_table_sections(rows):
    """Flatten explicit two-level headings and honor mid-table period changes."""
    width = max(map(len, rows))
    rows = [r + [''] * (width-len(r)) for r in rows]
    header = rows[0][:]
    start = 1
    # A blank first cell in the second row marks a vertically merged row label.
    # Monetary data cannot be treated as another header row.
    if (len(rows) > 2 and not rows[1][0] and any(not v for v in header[1:])
            and all(re.fullmatch(r'按.{1,12}|产品|地区|本报告期|本期|上年同期|期末|期初', v) for v in header[1:] if v)
            and sum(bool(v) for v in rows[1][1:]) >= 2
            and all(not re.search(r'\d', v) or re.fullmatch(r'20\d{2}年?', v) for v in rows[1][1:])):
        parent = ''
        for j in range(1, width):
            parent = header[j] or parent
            header[j] = ' / '.join(dict.fromkeys(v for v in (parent, rows[1][j]) if v))
        start = 2
    body = []
    for row in rows[start:]:
        period_header = (not row[0] and sum(bool(re.search(r'本报告期|本期|上年|期末|期初', v)) for v in row[1:]) >= 2
                         and not any(re.search(r'\d[,.]\d', v) for v in row[1:]))
        if period_header:
            if body:
                yield [header, *body]
            header, body = [header[0], *row[1:]], []
        else:
            body.append(row)
    if body:
        yield [header, *body]


def split_table(rows, max_rows=20, max_cols=12, *, normalized_headers=False):
    if max_rows < 1 or max_cols < 2:
        raise ValueError('invalid table split limits')
    for section in ([rows] if normalized_headers else normalize_table_sections(rows)):
        width = len(section[0])
        for start in range(1, width, max_cols-1):
            cols = [0, *range(start, min(width, start+max_cols-1))]
            for row in range(1, len(section), max_rows):
                yield [[r[c] for c in cols] for r in [section[0], *section[row:row+max_rows]]]


def text_simhash(body):
    """Use unweighted shingles to avoid simhash's uint8 weight overflow on NumPy 2.

    This preserves simhash 2.1's default lowercase/4-character feature semantics.
    Repeated features are passed individually instead of multiplying uint8 arrays.
    """
    from simhash import Simhash
    filtered = ''.join(re.findall(r'[\w\u4e00-\u9fcc]+', body.lower()))
    return Simhash([filtered[i:i+4] for i in range(max(len(filtered)-3, 1))]).value


def dedup(chunks, dist=3):
    seen, out = {}, []
    for c in sorted(chunks, key=lambda c: -c['section_level']):
        # Numeric table changes must never be collapsed by fuzzy hashing.
        key = (c['company'], c['source_sha256'], c['chunk_type'], c.get('parent_table_id', ''))
        body = c['text'].split('\n', 1)[1]
        previous = seen.setdefault(key, [])
        h = text_simhash(body) if c['chunk_type'] == 'text' else 0
        numbers = re.findall(r'[-+]?\d[\d,.]*%?', body)
        duplicate = any(body == old or (c['chunk_type'] == 'text' and numbers == nums
                        and (h ^ other).bit_count() <= dist) for old, other, nums in previous)
        if not duplicate:
            previous.append((body, h, numbers))
            out.append(c)
    return out


def chunk_pages(company, pages, count, *, source_sha256, company_code='', report_period='2025H1'):
    if not re.fullmatch(r'\d{4}H1', report_period):
        raise ValueError('M1 supports YYYYH1 reports only')
    chunks, buf, current_stack = [], [], None
    def make(stack, start, end, kind, body, **meta):
        issues = [issue for p in pages if start <= p.page_no <= end for issue in p.extraction_issues]
        if issues:
            meta['extraction_issues'] = issues
            meta['quality_flags'] = list(dict.fromkeys(meta.get('quality_flags', []) + ['native_table_rejected_on_page']))
        path = ' > '.join(t for _, t in stack) or '正文'
        c = dict(company=company, company_code=company_code, source_sha256=source_sha256,
                 report_period=report_period, section_path=path, section_level=stack[-1][0] if stack else 0,
                 page_start=start, page_end=end, chunk_type=kind, n_tokens=count(body),
                 text=f'【{company} | {report_period[:4]}年半年度报告 | {path}】\n{body}', **meta)
        c['chunk_id'] = stable_id(c)
        chunks.append(c)
    def flush():
        if buf:
            make(current_stack, buf[0][1], buf[-1][1], 'text', '\n'.join(t for t, _ in buf))
            buf.clear()
    for stack, page, (kind, item) in walk_sections(merge_cross_page_tables(pages)):
        if current_stack != stack or kind == 'table':
            flush()
        current_stack = stack
        if kind == 'para':
            for piece in split_bounded(item, count):
                if buf and count('\n'.join([*(t for t, _ in buf), piece])) > 450:
                    tail = buf[-1] if count(buf[-1][0]) <= 70 else None
                    flush()
                    if tail and count(tail[0]+'\n'+piece) <= 450:
                        buf.append(tail)
                buf.append((piece, page.page_no))
                if count('\n'.join(t for t, _ in buf)) >= 350:
                    flush()
        else:
            title = item.title_hint or (stack[-1][1] if stack else '表格')
            parent = stable_id([source_sha256, item.page_no, item.bbox, item.rows])
            for part, rows in enumerate(split_table(item.rows, normalized_headers=item.normalized_headers)):
                meta = dict(table_title=title, unit=item.unit, currency=item.currency, parent_table_id=parent, part=part, extraction_method=item.extraction_method, quality_status='unreviewed',
                            quality_flags=list(dict.fromkeys(item.quality_flags + (['unresolved_headers'] if any(not v for v in rows[0][1:]) else []))))
                if item.source_refs:
                    meta['source_table_refs'] = item.source_refs
                if item.unit_source:
                    meta['unit_source'] = item.unit_source
                head = f'表：{title}　单位：{item.unit or "未标注"}　币种：{item.currency or "未标注"}'
                make(stack, item.page_no, item.page_end or item.page_no, 'table_md', head+'\n'+to_md(rows), **meta)
                kv = []
                for row in rows[1:]:
                    values = [f'{rows[0][j] or str(j+1)+"列（列名未识别）"} {v}' for j, v in enumerate(row[1:], 1) if v]
                    if row[0] and values:
                        kv.append(f'{row[0]}：'+'，'.join(values))
                if kv:
                    make(stack, item.page_no, item.page_end or item.page_no, 'table_kv', head+'\n'+'\n'.join(kv), **meta)
    flush()
    return dedup(chunks)


def run(pdf_dir, out_path, company_map, tokenizer_path, company_metadata=None):
    from .extract import extract_pdf
    if not company_map:
        raise ValueError('companies.json is empty; provide confirmed report filenames and company names')
    paths = [(Path(pdf_dir)/fname, company) for fname, company in sorted(company_map.items())]
    for path, company in paths:
        if not path.is_file() or not isinstance(company, str) or not company.strip():
            raise ValueError(f'Invalid report mapping: {path}')
        if not path.resolve().is_relative_to(Path(pdf_dir).resolve()):
            raise ValueError('Report must be inside pdf_dir')
    if not Path(tokenizer_path).is_dir():
        raise ValueError('Provide an approved local tokenizer directory; automatic download is disabled')
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(tokenizer_path, local_files_only=True, trust_remote_code=False)
    count = lambda text: len(tokenizer.encode(text, add_special_tokens=False))
    result = []
    for path, company in paths:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        result.extend(chunk_pages(company, extract_pdf(path, company), count, source_sha256=digest,
                                   company_code=(company_metadata or {}).get(company, {}).get('company_code', '')))
    if not result:
        raise ValueError('No chunks extracted')
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation protects existing evidence datasets.
    with out.open('x', encoding='utf-8') as stream:
        for c in result:
            stream.write(json.dumps(c, ensure_ascii=False)+'\n')
    return len(result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pdf_dir', default='data/raw_pdf')
    parser.add_argument('--out', default='data/chunks.jsonl')
    parser.add_argument('--company_map', default='configs/companies.json')
    parser.add_argument('--tokenizer-path', required=True)
    parser.add_argument('--company-metadata', default='configs/company_metadata.json')
    args = parser.parse_args()
    try:
        mapping = json.loads(Path(args.company_map).read_text(encoding='utf-8'))
        metadata = json.loads(Path(args.company_metadata).read_text(encoding='utf-8'))
        total = run(args.pdf_dir, args.out, mapping, args.tokenizer_path, metadata)
        print(f'Wrote {total} chunks')
    except (ValueError, FileExistsError, FileNotFoundError) as exc:
        parser.error(str(exc))

if __name__ == '__main__':
    main()
