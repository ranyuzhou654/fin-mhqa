"""Deterministic views of Docling JSON; model inference is kept in a separate runtime."""
import re


def table_views(table):
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
    # Header flags may restart halfway through a native table. Only contiguous
    # header runs form a header; intervening numeric rows remain body evidence.
    header_rows = {r for r, row in enumerate(grid)
                   if any(i is not None and cells[i].get('column_header') for i in row)}
    sections = []
    start = 0
    while start < nrows:
        header_end = start
        while header_end < nrows and header_end in header_rows:
            header_end += 1
        # Dates/audit qualifications can be classified as body by the model.
        while header_end > start and header_end < nrows:
            ids = {i for i in grid[header_end] if i is not None and cells[i]['text'].strip()}
            first = grid[header_end][0]
            values = [cells[i]['text'] for i in ids]
            if ((first is None or not cells[first]['text'].strip()) and values
                    and all(re.search(r'月|日|期间|审计|重述|重列|附注', v) for v in values)):
                header_end += 1
            else:
                break
        end = header_end
        while end < nrows and end not in header_rows:
            end += 1
        # A later section can have fewer physical columns than the overall
        # native grid. Collapse only identical cell identities, never equal text.
        columns = []
        for col in range(ncols):
            signature = [grid[r][col] for r in range(start, end)]
            if columns and any(i is not None for i in signature) and signature == [grid[r][columns[-1]] for r in range(start, end)]:
                continue
            columns.append(col)
        headers = []
        for col in columns:
            ids = list(dict.fromkeys(grid[r][col] for r in range(start, header_end) if grid[r][col] is not None))
            headers.append(' / '.join(cells[i]['text'] for i in ids if cells[i]['text'].strip()))
        rows = [[cells[grid[r][col]]['text'] if grid[r][col] is not None else '' for col in columns] for r in range(header_end, end)]
        sections.append(dict(headers=headers, rows=rows, header_rows=header_end-start,
            row_start=start, row_end=end, source_table_ref=table.get('self_ref'),
            provenance=table.get('prov', []), cell_grid=[[row[col] for col in columns] for row in grid[start:end]], cells=cells, source_column_indices=columns,
            quality_status='unreviewed'))
        start = end
    return sections


def table_view(table):
    """Single-section compatibility API; never silently discard later sections."""
    views = table_views(table)
    if len(views) != 1:
        raise ValueError('Multiple header sections; use table_views')
    return views[0]


def top_left_bbox(box, height):
    if box['coord_origin'] == 'BOTTOMLEFT':
        return box['l'], height-box['t'], box['r'], height-box['b']
    if box['coord_origin'] == 'TOPLEFT':
        return box['l'], box['t'], box['r'], box['b']
    raise ValueError('Unknown coordinate origin')


def explicit_page_unit(text):
    """Recognize a scoped report declaration, not arbitrary mention of a unit."""
    header = re.sub(r'\s', '', text[:1000])
    match = re.search(r'除特别注明外[，,、]?金额单位为(人民币)?(百万元|千元|万元|亿元|元)', header)
    return (match[2], match[1] or '', match[0]) if match else ('', '', '')


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
                views = table_views(table)
            except ValueError as exc:
                # Preserve original page prose for malformed structures. Do not
                # resolve overlapping spans by silently choosing a cell value.
                issues[page_no].append(dict(source_ref=f'{path.as_posix()}{table["self_ref"]}',error=str(exc)))
                continue
            for view in views:
                # Single-column structures remain in the spatial prose stream.
                if len(view['headers']) < 2 or not view['rows']:
                    continue
                info, tables = by_page[page_no]
                bbox = top_left_bbox(prov['bbox'], info['size']['height'])
                flags = ['cell_bbox_unverified']
                if not view['header_rows']:
                    flags.append('native_header_missing')
                if len(views) > 1:
                    flags.append('native_table_split_header_sections')
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
            page_unit, page_currency, declaration = explicit_page_unit(text)
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
                if table.unit:
                    table.unit_source = 'table_local_explicit'
                elif page_unit:
                    table.unit = page_unit
                    table.unit_source = f'page {number}: {declaration}'
                if not table.currency and page_currency and table.unit in ('百万元', '千元', '万元', '亿元', '元'):
                    table.currency = page_currency
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
