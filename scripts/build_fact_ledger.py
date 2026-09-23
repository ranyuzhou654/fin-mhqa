"""Build the benchmark_v1 fact ledger from the admitted corpus; no model calls, no repairs."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
from finmhqa.bench.ledger import build_company, check_identities, check_corroboration, admit

STATEMENT = {'IS': '利润表', 'CF': '现金流量表', 'BS': '资产负债表'}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--chunks', type=Path, default=Path('index/chunk_meta.jsonl'))
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    if a.out.exists():
        raise FileExistsError(a.out)
    raw = a.chunks.read_bytes()
    chunks = [dict(json.loads(line), _order=i) for i, line in enumerate(raw.splitlines())]
    by_company = defaultdict(list)
    for c in chunks:
        by_company[c['company']].append(c)
    records, notes, corroboration, statements = [], [], {}, {}
    for company in sorted(by_company):
        rs, ns, corr, segments, unit_decl = build_company(company, by_company[company])
        records += rs; notes += ns; corroboration[company] = corr
        statements[company] = {'report_unit_declaration': {'unit': unit_decl[0], 'chunk_id': unit_decl[1]},
                               'statements': [{'statement': s['kind'], 'scope': s['scope'], 'pages': s['pages'],
                                               'chunks': [c['chunk_id'] for c in s['chunks']]} for s in segments]}
    check_identities(records)
    check_corroboration(records, corroboration)
    facts, review = [], []
    for r in records:
        if r['helper']:
            continue
        reasons = admit(r)
        r = {k: v for k, v in r.items() if k != 'helper'}
        r['fact_id'] = 'f-' + hashlib.sha256(f"{r['company']}|{r['scope']}|{r['metric']}|{r['period']}".encode()).hexdigest()[:12]
        (review if reasons else facts).append({**r, 'rejection_reasons': reasons} if reasons else r)
    a.out.mkdir(parents=True)
    write = lambda name, rows: (a.out / name).write_text(''.join(json.dumps(x, ensure_ascii=False) + '\n' for x in rows))
    write('facts.jsonl', facts)
    write('review.jsonl', review)
    write('extraction_notes.jsonl', notes)
    (a.out / 'statements.json').write_text(json.dumps(statements, ensure_ascii=False, indent=2) + '\n')
    total = len(facts) + len(review)
    summary = {'chunks_sha256': hashlib.sha256(raw).hexdigest(), 'candidates': total, 'admitted': len(facts), 'review': len(review),
               'admitted_by_scope': dict(Counter(f['scope'] for f in facts)),
               'admitted_by_metric': dict(Counter(f['metric'] for f in facts)),
               'admitted_by_company': dict(Counter(f['company'] for f in facts)),
               'confirmation': dict(Counter(('identity' if f['identity'] and f['identity']['passed'] else '') +
                                            ('+' if f['identity'] and f['identity']['passed'] and f['corroboration'] else '') +
                                            ('corroboration' if f['corroboration'] else '') for f in facts)),
               'rejection_reasons': dict(Counter(x for r in review for x in r['rejection_reasons'])),
               'code_sha256': {str(p): hashlib.sha256(Path(p).read_bytes()).hexdigest()
                               for p in ('src/finmhqa/bench/ledger.py', __file__)}}
    (a.out / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    lines = ['# 事实台账复核清单', '', f"候选 {total} 条，准入 {len(facts)} 条，待复核 {len(review)} 条。未准入的事实不进入评测集，也不做修补。", '']
    for r in sorted(review, key=lambda r: (r['company'], r['scope'], r['statement'], r['metric'], r['period'])):
        where = f"p{r['page']} `{r['chunk_id']}`" if r.get('chunk_id') else f"报表页 {r['statement_pages']}"
        lines.append(f"- **{r['company']}** {r['scope']} {STATEMENT[r['statement']]} {r['metric_label']} {r['period']}："
                     f"{r.get('raw_value', '—')} {r.get('unit') or ''}（{where}）— {'；'.join(r['rejection_reasons'])}")
    (a.out / 'REVIEW.md').write_text('\n'.join(lines) + '\n')
    print(json.dumps({k: summary[k] for k in ('candidates', 'admitted', 'review', 'confirmation', 'rejection_reasons')}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
