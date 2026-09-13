"""Conservative alignment-based fallback for explicit financial statement pages."""
import re

NUMBER = re.compile(r'^[（(\-−]?\d[\d,，]*(?:\.\d+)?[）)]?%?$')
STATEMENT = re.compile(r'[^\n]{0,35}(?:资产负债表|利润表|现金流量表)(?:[（(]续[）)])?')


def normalize_statement(rows):
    rows = [[(c or '').replace('\n',' ').strip() for c in row] for row in rows]
    rows = [row for row in rows if any(row)]
    if not rows:
        return None
    width = max(map(len,rows))
    rows = [row+['']*(width-len(row)) for row in rows]
    start = next((i for i,row in enumerate(rows)
                  if sum(bool(NUMBER.fullmatch(v.replace(' ',''))) for v in row) >= 2
                  and any(re.search(r'[\u4e00-\u9fff]',v) and not re.search(r'年|月|日|期间|季度',v) for v in row)), None)
    if start is None or start == 0 or start > 10:
        return None
    headings = [' '.join(dict.fromkeys(row[j] for row in rows[:start] if row[j])) for j in range(width)]
    # Require actual period headers; alignment alone cannot establish semantics.
    if sum(bool(re.search(r'20\d{2}|本期|上期|期末|期初',h)) for h in headings) < 2:
        return None
    boundary = next((j for j,h in enumerate(headings) if re.search(r'附注|20\d{2}|本期|上期|期末|期初',h)), None)
    if boundary is None or boundary == 0:
        return None
    header = ['项目'] + headings[boundary:]
    if any(not h or (not re.search(r'附注', h) and not re.search(r'20\d{2}|本期|上期|期末|期初', h)) for h in header[1:]):
        return None
    body = [[' '.join(row[:boundary]).strip()] + row[boundary:] for row in rows[start:]]
    return [header,*body]


def find_statement_tables(page, text):
    # This deliberately excludes prose discussion mentioning a statement name.
    match = next((STATEMENT.fullmatch(line.strip()) for line in text.splitlines()
                  if STATEMENT.fullmatch(line.strip())), None)
    if not match:
        return []
    result=[]
    for table in page.find_tables({'vertical_strategy':'text','horizontal_strategy':'text','min_words_vertical':3}):
        from pdfplumber.table import Table
        # Text alignment edges can bisect outdented labels or closing brackets.
        cells = [(0 if abs(c[0]-table.bbox[0])<.01 else c[0], c[1],
                  page.width if abs(c[2]-table.bbox[2])<.01 else c[2], c[3]) for c in table.cells]
        expanded = Table(page, cells)
        rows=normalize_statement(expanded.extract())
        if rows and len(rows)>=5:
            result.append((expanded.bbox,rows,match[0]))
    return result
