"""Bind fixed source-grounded questions to evidence chunks before running retrieval."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import pymupdf


def compact(s):return re.sub(r'\s','',s).replace('<br>','')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--chunks',default='data/candidates/docling/chunks.jsonl',type=Path)
    p.add_argument('--facts',default='data/qa/docling_corpus/fact_checks.json',type=Path)
    p.add_argument('--out',default='data/eval/retrieval_dev30.bound.jsonl',type=Path)
    a=p.parse_args()
    source=Path('configs/retrieval_dev30.jsonl')
    queries=[json.loads(l) for l in source.read_text().splitlines()]
    if len(queries)!=30 or len({q['query_id'] for q in queries})!=30:raise ValueError('Expected 30 distinct fixed queries')
    chunks=[json.loads(l) for l in a.chunks.read_text().splitlines()]
    digest=hashlib.sha256(a.chunks.read_bytes()).hexdigest()
    facts=json.loads(a.facts.read_text())
    if facts['dataset_sha256']!=digest:raise ValueError('Facts were checked against another dataset')
    fact_ids={(c['id'],c['row']):c['chunk_ids'] for c in facts['checks']}
    records={r['company_code']:r for r in json.loads(Path('data/source_manifest.json').read_text())}
    texts={}; result=[]
    for query in queries:
        record=records[query['company_code']]
        source_pdf=Path('data/raw_pdf')/record['filename']
        if query['source_sha256']!=record['sha256']:raise ValueError('Query source snapshot differs')
        if query['company_code'] not in texts:
            if hashlib.sha256(source_pdf.read_bytes()).hexdigest()!=record['sha256']:raise ValueError('Source PDF changed')
            with pymupdf.open(source_pdf) as pdf:texts[query['company_code']]=[compact(page.get_text()) for page in pdf]
        page_text=texts[query['company_code']][query['page']-1]
        if not all(compact(t) in page_text for t in query['expected_terms']):raise ValueError(f'Source terms missing: {query["query_id"]}')
        candidates=[c for c in chunks if c['company_code']==query['company_code'] and c['source_sha256']==query['source_sha256'] and c['page_start']<=query['page']<=c['page_end']]
        if 'source_anchor' in query:
            anchor=query['source_anchor']
            matched=set(fact_ids.get((anchor['id'],anchor['row']),[]))
            # Accept the paired KV form only for a Markdown part that passed the
            # source-row/header check; this does not depend on retrieval outputs.
            parts={(c['parent_table_id'],c['part']) for c in candidates if c['chunk_id'] in matched}
            ids=[c['chunk_id'] for c in candidates if c['chunk_id'] in matched or (c.get('parent_table_id'),c.get('part')) in parts]
        else:
            ids=[c['chunk_id'] for c in candidates if all(compact(t) in compact(c['text']) for t in query['expected_terms'])]
        result.append(dict(query,gold_chunk_ids=ids,label_status='candidate_bound' if ids else 'MISSING_EVIDENCE',dataset_sha256=digest))
    a.out.parent.mkdir(parents=True,exist_ok=True)
    with a.out.open('x') as stream:
        for item in result:stream.write(json.dumps(item,ensure_ascii=False)+'\n')
    report=dict(queries=len(result),bound=sum(bool(q['gold_chunk_ids']) for q in result),missing=[q['query_id'] for q in result if not q['gold_chunk_ids']],dataset_sha256=digest,query_source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),bound_sha256=hashlib.sha256(a.out.read_bytes()).hexdigest(),retrieval_evaluated=False)
    a.out.with_suffix('.summary.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
    if report['missing']:raise SystemExit(1)

if __name__=='__main__':main()
