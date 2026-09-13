"""Source-only equivalence candidates; never reads retrieval responses or assigns gold."""
import argparse
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import pymupdf


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def compact(text):return re.sub(r'\s+', '',text).replace(',','').replace('，','').replace('<br>','')
def contains(text,term):
    # PDF extraction may fuse adjacent numeric columns after whitespace removal.
    # Discovery deliberately over-includes; reviewed row/column assertions decide gold.
    return compact(term) in compact(text)


def anchors(q):
    if q['category']=='reported_revenue':
        value=Decimal(q['expected_terms'][1].replace(',',''))
        # Exact unit conversions for candidate discovery only; admission requires source review.
        variants=[format(x,'f') for x in (value,value*1000,value/1000)]
        variants += [v.rstrip('0').rstrip('.') for v in variants if '.' in v]
        return [['营业收入'],sorted(set(variants))]
    special={
        11:['23.27%','23.23%'],12:['3,126,636','1,760,709'],13:['537,660','348,937'],
        14:['8,766,551','7,662,534'],15:['217,044','732,802'],
        16:['7,558,994.89','1,376,252.94','4,000,959.89','4,934,287.94'],
        17:['国外','115','11'],18:['29,596,366','19,620,756'],19:['3,247,033','69,006'],
        20:['珠海格力集团财务有限责任公司','99.54','0.46'],21:['上海格力空调销售有限公司','90.00','9.70'],
        22:['872,526','12,511'],23:['82,116','13,256'],24:['22,340,270,649.95','2,618,084,499.81'],
        25:['19,351,535,309.06','2,519,647,921.00'],26:['共62家单位','1,194,962,161.99','预计难以收回']}
    n=int(q['query_id'].rsplit('-',1)[1])
    return [[s] for s in special.get(n,q['expected_terms'])]


def discover(out):
    root=Path(out);root.mkdir(parents=True,exist_ok=False)
    source=Path('configs/retrieval_dev30.jsonl');dataset=Path('data/candidates/docling/chunks.jsonl')
    queries=[json.loads(l) for l in source.read_text().splitlines()]
    chunks=[json.loads(l) for l in dataset.read_text().splitlines()]
    reports={r['company_code']:r for r in json.loads(Path('data/source_manifest.json').read_text())}
    pages={}
    for code,r in reports.items():
        path=Path('data/raw_pdf')/r['filename']
        if sha(path)!=r['sha256']:raise ValueError('Source PDF changed')
        with pymupdf.open(path) as pdf:pages[code]=[p.get_text(sort=True) for p in pdf]
    rows=[]
    for q in queries:
        groups=anchors(q)
        match=lambda text:all(any(contains(text,t) for t in group) for group in groups)
        page_ids=[i+1 for i,t in enumerate(pages[q['company_code']]) if match(t)]
        found=[c for c in chunks if c['company_code']==q['company_code'] and c['source_sha256']==q['source_sha256'] and match(c['text'])]
        item=dict(query_id=q['query_id'],query=q['query'],anchor_groups=groups,source_pages=page_ids,candidates=found)
        rows.append(item)
        (root/(q['query_id']+'.json')).write_text(json.dumps(item,ensure_ascii=False,indent=2)+'\n')
        print(q['query_id'],'source pages',page_ids,'candidate pages',sorted({c['page_start'] for c in found}),'chunks',len(found))
    summary=dict(status='CANDIDATES_ONLY',query_source_sha256=sha(source),dataset_sha256=sha(dataset),
        source_manifest_sha256=sha('data/source_manifest.json'),queries=[dict(query_id=r['query_id'],source_pages=r['source_pages'],candidate_ids=[c['chunk_id'] for c in r['candidates']]) for r in rows],
        limitations='Exact anchor discovery is not exhaustive semantic annotation. Every candidate requires a source-grounded accept/reject decision; no retrieval results used.')
    (root/'discovery.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',default='data/eval/equivalence_v2');discover(p.parse_args().out)
