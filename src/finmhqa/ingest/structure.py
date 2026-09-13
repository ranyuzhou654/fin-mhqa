from copy import deepcopy
import re
from .clean import clean_text, heading_level, is_toc_page


def merge_cross_page_tables(pages):
    """Merge only geometrically adjacent tables with matching metadata; retain input."""
    pages = deepcopy(pages)
    active = None
    last_page = None
    for page in pages:
        if page.tables and active and last_page:
            candidate = page.tables[0]
            same = (page.company == last_page.company and page.page_no == last_page.page_no + 1
                    and last_page.height > 0 and page.height > 0
                    and active.bbox[3] >= last_page.height * .85 and candidate.bbox[1] <= page.height * .2
                    and len(active.rows[0]) == len(candidate.rows[0])
                    and all(not getattr(candidate, k) or getattr(candidate, k) == getattr(active, k)
                            for k in ('title_hint', 'unit', 'currency')))
            header = candidate.rows[0] == active.rows[0]
            new_header = any(v in ' '.join(candidate.rows[0]) for v in ('项目', '本期', '上年同期', '金额'))
            # Native normalized tables merge only with a nonempty exact header.
            # Headerless continuation rows remain separate and explicitly unresolved.
            if active.normalized_headers or candidate.normalized_headers:
                same = same and active.normalized_headers and candidate.normalized_headers and header and any(candidate.rows[0])
            if same and (header or not new_header):
                active.rows.extend(candidate.rows[1:] if header else candidate.rows)
                active.page_end = page.page_no
                active.source_refs.extend(candidate.source_refs)
                active.quality_flags = list(dict.fromkeys(active.quality_flags + candidate.quality_flags))
                active.bbox = (active.bbox[0], active.bbox[1], active.bbox[2], candidate.bbox[3])
                page.tables.pop(0)
            else:
                active = None
        elif not page.tables:
            active = None
        if page.tables:
            active = page.tables[-1]
        last_page = page
    return pages


def walk_sections(pages):
    stack = []
    statement_base = None
    for page in pages:
        if is_toc_page(page.text):
            continue
        if page.tables and not page.lines and not page.spatial_extracted:
            raise ValueError('Table section attribution requires spatial text lines')
        events = [(y, 'para', value) for y, value in page.lines] if page.lines or page.spatial_extracted else [(i, 'para', v) for i, v in enumerate(page.text.splitlines())]
        events += [(table.bbox[1], 'table', table) for table in page.tables]
        ordered = sorted(events, key=lambda e: e[0])
        combined = []
        for event in ordered:
            if (combined and combined[-1][1] == 'para' and event[1] == 'para'
                    and re.fullmatch(r'(?:第[一二三四五六七八九十百]+节|[（(][一二三四五六七八九十百]+[）)])', combined[-1][2].strip())):
                y, kind, marker = combined.pop()
                combined.append((y, kind, marker + ' ' + event[2]))
            else:
                combined.append(event)
        for _, kind, item in combined:
            if kind == 'table':
                yield list(stack), page, (kind, item)
                continue
            if re.fullmatch(r'\s*\d+\s*/\s*\d+\s*', item):
                continue
            for line in clean_text(item).splitlines():
                level = heading_level(line)
                if level:
                    if level == 1:
                        statement_base = None
                    if re.fullmatch(r'\d+[、.]\s*(?:合并|公司|母公司).{0,12}(?:资产负债表|利润表|现金流量表|所有者权益变动表)(?:[（(]续[）)])?', line):
                        if statement_base is None:
                            statement_base = [(lv, title) for lv, title in stack if lv < level]
                        stack = list(statement_base)
                    while stack and stack[-1][0] >= level:
                        stack.pop()
                    stack.append((level, line))
                elif line:
                    yield list(stack), page, ('para', line)
