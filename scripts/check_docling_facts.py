"""Check source-image transcribed anchors without interpreting numeric values."""
import json
from pathlib import Path
import re

def compact(text):return re.sub(r'\s','',text)

def main():
    root=Path('data/qa/docling_pilot')
    checks=json.loads(Path('configs/docling_pilot_checks.json').read_text())
    report=[]
    for check in checks:
        view=json.loads((root/check['id']/'views.json').read_text())[check['table']]
        rows=[r for r in view['rows'] if compact(r[0])==compact(check['row'])]
        row_ok=len(rows)==1 and list(map(compact,rows[0]))==list(map(compact,check['values']))
        head_ok=set(map(compact,check['headers'])).issubset(set(map(compact,view['headers'])))
        report.append(dict(id=check['id'],table=check['table'],row=check['row'],row_pass=row_ok,headers_pass=head_ok))
    (root/'fact_checks.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))
    if not all(r['row_pass'] and r['headers_pass'] for r in report):raise SystemExit(1)

if __name__=='__main__':main()
