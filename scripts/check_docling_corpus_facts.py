"""Check final Markdown rows and column headers against pre-existing source anchors."""
import argparse
import hashlib
import json
from pathlib import Path
import re


def compact(value):return re.sub(r'\s','',value).replace('<br>','')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--chunks',default='data/candidates/docling/chunks.jsonl',type=Path)
    p.add_argument('--out',default='data/qa/docling_corpus/fact_checks.json',type=Path)
    a=p.parse_args()
    chunks=[json.loads(l) for l in a.chunks.read_text().splitlines()]
    samples={s['id']:s for s in json.loads(Path('configs/docling_pilot.json').read_text())['samples']}
    checks=json.loads(Path('configs/docling_pilot_checks.json').read_text())
    rows=[]
    for check in checks:
        sample=samples[check['id']]
        matches=[]
        for chunk in chunks:
            if chunk['company_code']!=sample['filename'][:6] or chunk['chunk_type']!='table_md' or not chunk['page_start']<=sample['page']<=chunk['page_end']:continue
            lines=chunk['text'].splitlines()
            table=[list(map(compact,line.strip().strip('|').split('|'))) for line in lines if line.startswith('|')]
            expected=list(map(compact,check['values']))
            if table and expected in table[2:] and set(map(compact,check['headers'])).issubset(set(table[0])):
                matches.append(chunk['chunk_id'])
        rows.append(dict(id=check['id'],row=check['row'],passed=bool(matches),chunk_ids=matches))
    result=dict(dataset_sha256=hashlib.sha256(a.chunks.read_bytes()).hexdigest(),passed=sum(r['passed'] for r in rows),total=len(rows),checks=rows)
    a.out.parent.mkdir(parents=True,exist_ok=True)
    a.out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if not all(r['passed'] for r in rows):raise SystemExit(1)

if __name__=='__main__':main()
