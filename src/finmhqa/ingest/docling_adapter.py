"""Deterministic views of Docling JSON; model inference is kept in a separate runtime."""
import re


def table_view(table):
    data = table['data']
    nrows, ncols = data['num_rows'], data['num_cols']
    grid = [[None for _ in range(ncols)] for _ in range(nrows)]
    cells = data['table_cells']
    for i, c in enumerate(cells):
        for r in range(c['start_row_offset_idx'], c['end_row_offset_idx']):
            for col in range(c['start_col_offset_idx'], c['end_col_offset_idx']):
                if not (0 <= r < nrows and 0 <= col < ncols):
                    raise ValueError('Cell span outside table dimensions')
                if grid[r][col] is not None:
                    raise ValueError('Overlapping Docling cell spans')
                grid[r][col] = i
    header_end = max((c['end_row_offset_idx'] for c in cells if c.get('column_header')), default=0)
    # Retain continuation dates/audit qualifications misclassified as body rows.
    while header_end < nrows:
        ids = {i for i in grid[header_end] if i is not None and cells[i]['text'].strip()}
        first = grid[header_end][0]
        if first is not None and cells[first]['text'].strip():
            break
        values = [cells[i]['text'] for i in ids]
        if values and all(re.search(r'月|日|期间|审计|重述|重列|附注', v) for v in values):
            header_end += 1
        else:
            break
    headers = []
    for col in range(ncols):
        ids = list(dict.fromkeys(grid[r][col] for r in range(header_end) if grid[r][col] is not None))
        headers.append(' / '.join(cells[i]['text'] for i in ids if cells[i]['text'].strip()))
    rows = []
    for r in range(header_end, nrows):
        rows.append([cells[i]['text'] if i is not None else '' for i in grid[r]])
    return dict(headers=headers, rows=rows, header_rows=header_end,
                source_table_ref=table.get('self_ref'), provenance=table.get('prov', []),
                # Native cell IDs allow callers to avoid double counting a rowspan.
                cell_grid=grid, cells=cells, quality_status='pilot_unreviewed')


def top_left_bbox(box, height):
    if box['coord_origin'] == 'BOTTOMLEFT':
        return box['l'], height-box['t'], box['r'], height-box['b']
    if box['coord_origin'] == 'TOPLEFT':
        return box['l'], box['t'], box['r'], box['b']
    raise ValueError('Unknown coordinate origin')


def pages_from_native(pdf_path, company, native_batches, *, expected_sha256):
    """Native table structure plus spatial PDF text; exact full-page coverage required.

    Cell boxes are retained in native files, never exposed as verified citations.
    Source references are qualified by batch because Docling refs restart per batch.
    """
    import hashlib
    import json
    from pathlib import Path
    import pymupdf
    from .extract import Page, RawTable, parse_table_header
    pdf_path = Path(pdf_path)
    if hashlib.sha256(pdf_path.read_bytes()).hexdigest() != expected_sha256:
        raise ValueError('Source hash mismatch')
    by_page = {}
    issues = {}
    signature = None
    for path in sorted(map(Path, native_batches)):
        payload = json.loads(path.read_text())
        if payload['source_sha256'] != expected_sha256:
            raise ValueError(f'Stale native source: {path}')
        if signature is not None and signature != payload['settings_sha256']:
            raise ValueError('Mixed native conversion settings')
        signature = payload['settings_sha256']
        document = payload['document']
        for key, info in document['pages'].items():
            number = int(key)
            if number in by_page:
                raise ValueError(f'Duplicate native page: {number}')
            by_page[number] = (info, [])
            issues[number] = []
        for table in document['tables']:
            if len(table.get('prov', [])) != 1:
                raise ValueError('Expected one page provenance per native table')
            prov = table['prov'][0]
            page_no = prov['page_no']
            if str(page_no) not in document['pages']:
                raise ValueError('Native table references a page outside its batch')
            try:
                view = table_view(table)
            except ValueError as exc:
                # Preserve original page prose for malformed structures. Do not
                # resolve overlapping spans by silently choosing a cell value.
                issues[page_no].append(dict(source_ref=f'{path.as_posix()}{table["self_ref"]}',error=str(exc)))
                continue
            # Single-column structures cannot produce a KV view, so keep them
            # in the original spatial prose stream rather than dropping content.
            if len(view['headers']) < 2 or not view['rows']:
                continue
            info, tables = by_page[page_no]
            bbox = top_left_bbox(prov['bbox'], info['size']['height'])
            flags = ['cell_bbox_unverified']
            if not view['header_rows']:
                flags.append('native_header_missing')
            tables.append(RawTable(page_no, [view['headers'], *view['rows']], bbox,
                page_end=page_no, extraction_method='docling_accurate', normalized_headers=True,
                source_refs=[f'{path.as_posix()}{table["self_ref"]}'], quality_flags=flags))
    result = []
    with pymupdf.open(pdf_path) as pdf:
        if set(by_page) != set(range(1, len(pdf)+1)):
            raise ValueError('Native corpus page coverage incomplete')
        for number, page in enumerate(pdf, 1):
            info, tables = by_page[number]
            if abs(info['size']['height']-page.rect.height) > 1 or abs(info['size']['width']-page.rect.width) > 1:
                raise ValueError('Native and PDF page geometry differ')
            text = page.get_text('text', sort=True)
            if not text.strip():
                raise ValueError(f'Page {number} requires OCR decision')
            all_lines = []
            for block in page.get_text('dict', sort=True)['blocks']:
                for line in block.get('lines', []):
                    value = ''.join(span['text'] for span in line['spans']).strip()
                    if value:
                        all_lines.append((line['bbox'], value))
            tables.sort(key=lambda t: (t.bbox[1], t.bbox[0]))
            for table in tables:
                above = '\n'.join(value for box, value in all_lines
                    if table.bbox[1]-60 <= box[1] and box[3] <= table.bbox[1]+1)
                table.title_hint, table.unit, table.currency = parse_table_header(above)
                # Embedded declarations are explicit too; do not infer units from numbers.
                inside = '\n'.join(' '.join(row) for row in table.rows[:2])
                _, unit, currency = parse_table_header(inside)
                table.unit = table.unit or unit
                table.currency = table.currency or currency
            lines = []
            for box, value in all_lines:
                x, y = (box[0]+box[2])/2, (box[1]+box[3])/2
                if any(t.bbox[0] <= x <= t.bbox[2] and t.bbox[1] <= y <= t.bbox[3] for t in tables):
                    continue
                if re.fullmatch(r'\d+(?:\s*/\s*\d+)?', value) and box[1] > page.rect.height*.9:
                    continue
                lines.append((box[1], value))
            result.append(Page(company, number, text, tables, lines, page.rect.height, True, issues[number]))
    return result
