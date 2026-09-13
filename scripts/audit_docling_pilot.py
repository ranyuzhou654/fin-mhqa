"""Compare every numeric Docling cell against source PDF characters in its bbox."""
from collections import Counter
import json
from pathlib import Path
import re
import pymupdf
from finmhqa.ingest.docling_adapter import table_view

NUM = re.compile(r'^[（(\-−]?\d[\d,，]*(?:\.\d+)?[）)]?%?$')
compact = lambda text: re.sub(r'\s','',text)


def main():
    root=Path('data/qa/docling_pilot')
    samples=json.loads(Path('configs/docling_pilot.json').read_text())['samples']
    report=[]
    for sample in samples:
        dest=root/sample['id']
        document=json.loads((dest/'document.json').read_text())
        with pymupdf.open(Path('data/raw_pdf')/sample['filename']) as pdf:
            page=pdf[sample['page']-1]
            chars=[]
            for block in page.get_text('rawdict')['blocks']:
                for line in block.get('lines',[]):
                    for span in line['spans']:
                        chars.extend(span['chars'])
            checks=[];views=[]
            for ti,table in enumerate(document['tables']):
                views.append(table_view(table))
                for ci,cell in enumerate(table['data']['table_cells']):
                    value=compact(cell['text'])
                    if not NUM.fullmatch(value):continue
                    b=cell.get('bbox')
                    if not b:
                        checks.append(dict(table=ti,cell=ci,value=value,status='no_bbox'));continue
                    top,bottom=(b['t'],b['b']) if b['coord_origin']=='TOPLEFT' else (page.rect.height-b['t'],page.rect.height-b['b'])
                    selected=[c['c'] for c in chars if b['l']-0.6 <= (c['bbox'][0]+c['bbox'][2])/2 <= b['r']+0.6
                              and top-1 <= (c['bbox'][1]+c['bbox'][3])/2 <= bottom+1]
                    actual=compact(''.join(selected))
                    checks.append(dict(table=ti,cell=ci,value=value,pdf_chars=actual,
                                       status='match' if value==actual else 'mismatch'))
            (dest/'views.json').write_text(json.dumps(views,ensure_ascii=False,indent=2)+'\n')
            (dest/'numeric_audit.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2)+'\n')
            item=dict(id=sample['id'],tables=len(views),numeric_cells=len(checks),statuses=dict(Counter(c['status'] for c in checks)))
            report.append(item)
    (root/'numeric_summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
