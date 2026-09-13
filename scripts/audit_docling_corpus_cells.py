"""Measure native numeric-cell coordinate agreement, independently of chunk QA.

Uses the pilot's unchanged tolerance. A match checks text at a box only, not
row/column meaning; no acceptance threshold is inferred from this diagnostic.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import pymupdf
from audit_docling_pilot import NUM, compact
from finmhqa.ingest.docling_adapter import top_left_bbox


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--native-root',required=True,type=Path)
    p.add_argument('--out',default='data/qa/docling_corpus/cell_coordinates',type=Path)
    a=p.parse_args();a.out.mkdir(parents=True,exist_ok=True)
    summary=[]
    for record in json.loads(Path('data/source_manifest.json').read_text()):
        source=Path('data/raw_pdf')/record['filename']
        if hashlib.sha256(source.read_bytes()).hexdigest()!=record['sha256']:raise ValueError('Source hash mismatch')
        statuses=Counter();seen=set()
        target=a.out/f'{record["company_code"]}.jsonl'
        with pymupdf.open(source) as pdf, target.open('x',encoding='utf-8') as stream:
            for batch in sorted((a.native_root/record['company_code']).glob('*.json')):
                payload=json.loads(batch.read_text())
                if payload['source_sha256']!=record['sha256']:raise ValueError('Native source mismatch')
                document=payload['document']
                numbers=set(map(int,document['pages']))
                if seen & numbers:raise ValueError('Duplicate pages')
                seen.update(numbers)
                char_cache={}
                for table in document['tables']:
                    page_no=table['prov'][0]['page_no'];page=pdf[page_no-1]
                    if page_no not in char_cache:
                        chars=[]
                        for block in page.get_text('rawdict')['blocks']:
                            for line in block.get('lines',[]):
                                for span in line['spans']:
                                    chars.extend((c['c'],(c['bbox'][0]+c['bbox'][2])/2,(c['bbox'][1]+c['bbox'][3])/2) for c in span['chars'])
                        char_cache[page_no]=chars
                    for i,cell in enumerate(table['data']['table_cells']):
                        value=compact(cell['text'])
                        if not NUM.fullmatch(value):continue
                        box=cell.get('bbox');actual=''
                        if box:
                            l,t,r,b=top_left_bbox(box,page.rect.height)
                            actual=compact(''.join(c for c,x,y in char_cache[page_no] if l-.6<=x<=r+.6 and t-1<=y<=b+1))
                        status='no_bbox' if not box else ('match' if value==actual else 'mismatch')
                        statuses[status]+=1
                        stream.write(json.dumps(dict(page=page_no,source_ref=f'{batch}{table["self_ref"]}',cell=i,value=value,pdf_chars=actual,status=status),ensure_ascii=False)+'\n')
            if seen!=set(range(1,record['pages']+1)):raise ValueError('Incomplete corpus')
        item=dict(company=record['company'],numeric_cells=sum(statuses.values()),statuses=dict(statuses),details=str(target),sha256=hashlib.sha256(target.read_bytes()).hexdigest())
        summary.append(item);print(json.dumps(item,ensure_ascii=False),flush=True)
    (a.out/'summary.json').write_text(json.dumps(dict(tolerance=dict(x=.6,y=1),metric='coordinate_text_agreement_only',companies=summary),ensure_ascii=False,indent=2)+'\n')

if __name__=='__main__':main()
