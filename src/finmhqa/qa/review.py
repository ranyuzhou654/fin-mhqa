"""M1 review scoring; unreviewed/altered samples never produce acceptance."""
from collections import Counter

LABELS = {'breadcrumb_correct', 'table_correct', 'review_notes'}


def score_review(chunks, samples, expected_sample_ids):
    by_id = {chunk['chunk_id']: chunk for chunk in chunks}
    if len(by_id) != len(chunks):
        raise ValueError('Duplicate dataset chunk IDs')
    sample_ids = [sample['chunk_id'] for sample in samples]
    if len(set(sample_ids)) != len(sample_ids) or set(sample_ids) != set(expected_sample_ids):
        raise ValueError('Review samples differ from the fixed audit sample')
    companies = sorted({c['company'] for c in chunks})
    counts, stats = Counter(), {c: Counter() for c in companies}
    for sample in samples:
        original = by_id.get(sample['chunk_id'])
        content = {k:v for k,v in sample.items() if k not in LABELS}
        if content != original:
            raise ValueError(f'Reviewed evidence changed: {sample["chunk_id"]}')
        for field in ('breadcrumb_correct', 'table_correct'):
            if sample.get(field) is not None and type(sample[field]) is not bool:
                raise ValueError(f'{field} must be boolean or null')
        company, kind = sample['company'], sample['chunk_type']
        counts[company, kind] += 1
        stats[company]['sample_count'] += 1
        breadcrumb = sample.get('breadcrumb_correct')
        table = sample.get('table_correct')
        if breadcrumb is None or (kind.startswith('table') and table is None):
            stats[company]['pending'] += 1
        else:
            stats[company]['reviewed'] += 1
            stats[company]['breadcrumb_pass'] += breadcrumb
            stats[company]['joint_pass'] += breadcrumb and (not kind.startswith('table') or table)
        if kind.startswith('table'):
            stats[company]['table_count'] += 1
            stats[company]['table_pass'] += table is True
    complete = len(companies) == 10 and all(counts[c,k] == 10 for c in companies for k in ('text','table_md','table_kv'))
    rows=[]
    for company, stat in stats.items():
        total=stat['sample_count']
        rows.append(dict(company=company,**stat,
            breadcrumb_rate=stat['breadcrumb_pass']/total if total else 0,
            table_rate=stat['table_pass']/stat['table_count'] if stat['table_count'] else 0,
            joint_rate=stat['joint_pass']/total if total else 0))
    pending = not complete or any(s['pending'] for s in stats.values())
    quality = all(r['breadcrumb_rate'] >= .95 and r['table_rate'] >= .95 and r['joint_rate'] >= .95 for r in rows)
    size_ok = 6000 <= len(chunks) <= 10000
    status = 'PENDING_REVIEW' if pending else ('PASSED' if quality and size_ok else 'FAILED')
    return dict(status=status,complete_sample=complete,chunk_count=len(chunks),chunk_count_target_met=size_ok,companies=rows)
