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
