"""PDF extraction with spatial ordering and explicit failures (no automatic OCR)."""
from dataclasses import dataclass, field
import re

@dataclass
class RawTable:
    page_no: int
    rows: list[list[str]]
    bbox: tuple
    title_hint: str = ''
    unit: str = ''
    currency: str = ''
    page_end: int = 0
    extraction_method: str = 'lines'
    normalized_headers: bool = False
    source_refs: list[str] = field(default_factory=list)
    quality_flags: list[str] = field(default_factory=list)

@dataclass
class Page:
    company: str
    page_no: int
    text: str
    tables: list[RawTable] = field(default_factory=list)
    # Text line coordinates permit assigning each table to its actual section.
    lines: list[tuple[float, str]] = field(default_factory=list)
    height: float = 0
    spatial_extracted: bool = False
    extraction_issues: list[dict] = field(default_factory=list)

UNIT = re.compile(r'单位\s*[:：]\s*(?:人民币)?\s*(百万元|千元|万元|亿元|元|万股|股|万吨|吨|个|人)')
CUR = re.compile(r'币种\s*[:：]\s*(人民币|美元|港币)')

def parse_table_header(text):
    unit, currency = UNIT.search(text), CUR.search(text)
    title = next((line.strip() for line in reversed(text.splitlines())
                  if len(line.strip()) < 60 and not UNIT.search(line) and not CUR.search(line)
                  and re.search(r'表$|情况|构成|明细|数据|指标', line)), '')
    return title, unit[1] if unit else '', currency[1] if currency else ''

def extract_pdf(path, company):
    import pymupdf
    import pdfplumber
    pages = []
    statement_unit = ''
    with pymupdf.open(path) as doc, pdfplumber.open(path) as pl:
        for i, page in enumerate(doc):
            text = page.get_text('text', sort=True)
            if not text.strip():
                raise ValueError(f'{path}: page {i+1} has no text; OCR decision required')
            declaration = re.search(r'(?:财务报表及附注的单位为|金额单位为)(?:[:：])?\s*(?:人民币)?\s*(百万元|千元|万元|亿元|元)', text)
            if declaration:
                statement_unit = declaration[1]
            tables = []
            try:
                pp = pl.pages[i]
                for table in pp.find_tables():
                    extracted = table.extract()
                    row_objects = list(table.rows)
                    # Decorative page frames can join a real table and turn the
                    # report header into a single spanning first cell.
                    while len(extracted) > 2 and sum(bool(c) for c in extracted[0]) == 1 and re.search(r'报告|报表附注', ' '.join(c or '' for c in extracted[0])):
                        extracted.pop(0)
                        row_objects.pop(0)
                    while len(extracted) > 2 and sum(bool(c) for c in extracted[-1]) <= 1:
                        tail = ' '.join(c or '' for c in extracted[-1]).strip()
                        if tail and not re.fullmatch(r'\d+(?:\s*/\s*\d+)?', tail):
                            break
                        extracted.pop()
                        row_objects.pop()
                    rows = [[(c or '').replace('\n', ' ').strip() for c in row] for row in extracted]
                    rows = [row for row in rows if any(row)]
                    if len(rows) < 2 or max(map(len, rows)) < 2:
                        continue
                    bbox = (table.bbox[0], row_objects[0].bbox[1], table.bbox[2], row_objects[-1].bbox[3])
                    top = bbox[1]
                    above = pp.crop((0, max(0, top-60), pp.width, top)).extract_text() if top > 0 else ''
                    tables.append(RawTable(i+1, rows, bbox, *parse_table_header(above or ''), i+1))
                if not tables:
                    from .borderless import find_statement_tables
                    for bbox, rows, title in find_statement_tables(pp, text):
                        _, unit, currency = parse_table_header(text[:500])
                        tables.append(RawTable(i+1, rows, bbox, title, unit or statement_unit, currency, i+1, 'text_alignment'))
            except Exception as exc:
                raise RuntimeError(f'{path}: table extraction failed on page {i+1}') from exc
            lines = []
            for block in page.get_text('dict', sort=True)['blocks']:
                for line in block.get('lines', []):
                    x0, y0, x1, y1 = line['bbox']
                    # Remove only text whose center lies inside an extracted table.
                    if any(t.bbox[0] <= (x0+x1)/2 <= t.bbox[2] and t.bbox[1] <= (y0+y1)/2 <= t.bbox[3] for t in tables):
                        continue
                    value = ''.join(s['text'] for s in line['spans']).strip()
                    if re.fullmatch(r'\d+(?:\s*/\s*\d+)?', value) and y0 > page.rect.height * .90:
                        continue
                    if value:
                        lines.append((y0, value))
            pages.append(Page(company, i+1, text, tables, lines, page.rect.height))
    return pages
