"""Collect the initial 2025H1 disclosure snapshot from CNInfo (no model API)."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import requests
import pymupdf

COMPANIES = [('000333','美的集团','家电'),('000651','格力电器','家电'),('600690','海尔智家','家电'),
             ('002594','比亚迪','新能源汽车'),('300750','宁德时代','电池'),('300014','亿纬锂能','电池'),
             ('600519','贵州茅台','白酒'),('000858','五粮液','白酒'),('000568','泸州老窖','白酒'),('600887','伊利股份','乳制品')]

def collect(item):
    code, company, industry = item
    root = Path('data/raw_pdf')
    root.mkdir(parents=True, exist_ok=True)
    response = requests.post('https://www.cninfo.com.cn/new/hisAnnouncement/query', data={
        'pageNum':1,'pageSize':30,'column':'szse','tabName':'fulltext','searchkey':('五 粮 液' if code == '000858' else code),
        'seDate':'2025-07-01~2025-09-01','category':'category_bndbg_szsh;','isHLtitle':'false'}, timeout=60)
    response.raise_for_status()
    payload = response.json()
    candidates = [a for a in (payload.get('announcements') or []) if a['secCode'] == code
                  and re.search(r'2025年半年度报告(?:全文)?(?:（更新前）)?$', re.sub(r'<[^>]+>|\s','',a['announcementTitle']))]
    if len(candidates) != 1:
        raise ValueError(f'{company}: expected one full report, got {[(a["announcementTitle"],a["adjunctUrl"]) for a in candidates]}')
    a = candidates[0]
    url = a['adjunctUrl'] if a['adjunctUrl'].startswith('https://') else 'https://static.cninfo.com.cn/' + a['adjunctUrl']
    path = root / f'{code}_{company}_2025H1.pdf'
    if not path.exists():
        r = requests.get(url, timeout=120)
        r.raise_for_status()
        if not r.content.startswith(b'%PDF'):
            raise ValueError(f'{company}: not PDF')
        path.write_bytes(r.content)
    with pymupdf.open(path) as doc:
        front = ''.join(doc[i].get_text() for i in range(min(10,len(doc))))
        compact = re.sub(r'\s','',front)
        if len(doc)<40 or '2025年半年度报告' not in compact or code not in compact:
            raise ValueError(f'{company}: identity/full-report verification failed')
        count = len(doc)
    record = dict(company=company,company_code=code,industry=industry,report_period='2025H1',
                  filename=path.name,source_url=url,announcement_id=a['announcementId'],
                  publication_date=datetime.fromtimestamp(a['announcementTime']/1000,timezone.utc).strftime('%Y-%m-%d'),
                  retrieved_at=datetime.now(timezone.utc).isoformat(),sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                  bytes=path.stat().st_size,pages=count,snapshot_policy='2025 initial disclosure; excludes later restatements')
    (root / (path.stem+'.source.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(company,count,url,flush=True)
    return record

def main():
    with ThreadPoolExecutor(max_workers=3) as pool:
        records = list(pool.map(collect, COMPANIES))
    Path('data/source_manifest.json').write_text(json.dumps(records,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    Path('configs/companies.json').write_text(json.dumps({r['filename']:r['company'] for r in records},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    Path('configs/company_metadata.json').write_text(json.dumps({r['company']:{'company_code':r['company_code'],'industry':r['industry']} for r in records},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

if __name__ == '__main__':
    main()
