"""Offline pilot scoring; gold is loaded only after execution has ended."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
from decimal import Decimal

def norm(s):return re.sub(r'[\s,，]','',str(s))
def metric_match(metric,text):
    text=norm(text)
    if '净利润' in metric and ('股东' in metric or '母公司' in metric):
        return '净利润' in text and ('股东' in text or '母公司' in text)
    return norm(metric) in text

def main():
    p=argparse.ArgumentParser();p.add_argument('--gold',type=Path,required=True);p.add_argument('--freeze',type=Path,required=True);p.add_argument('--run',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    if a.out.exists():raise FileExistsError(a.out)
    frozen=json.loads(a.freeze.read_text());manifest=json.loads((a.run/'manifest.json').read_text())
    if hashlib.sha256(a.gold.read_bytes()).hexdigest()!=frozen['gold_sha256'] or manifest['questions_sha256']!=frozen['questions_sha256']:raise ValueError('frozen inputs changed')
    gold=list(map(json.loads,a.gold.read_text().splitlines()));expected={r['id']:r for r in frozen['results']}
    results=[];total_usage=Counter()
    for row in gold:
        folder=a.run/row['id']
        if not (folder/'result.json').exists():
            results.append({'id':row['id'],'status':'NOT_RUN','correct':False});continue
        r=json.loads((folder/'result.json').read_text());trajectory=list(map(json.loads,(folder/'trajectory.jsonl').read_text().splitlines()))
        final=r['final'];correct=False
        if not r['error'] and final and final['status']=='answered':
            try:correct=Decimal(final['answer_value'])==Decimal(row['answer_value']) and final['answer_unit']==row['answer_unit']
            except Exception:pass
            if row['type']=='bridge':correct=correct and final['selected_company']==expected[row['id']]['selected_company']
        chunks={c['chunk_id']:c for e in trajectory if e['event']=='tool_response' and e['tool']=='search' for c in e['response']['results']}
        citations=set(final['citations']) if final else set()
        hits=[];cited=[]
        for h in row['hops']:
            matching=[cid for cid,c in chunks.items() if c['company_code']==h['company_code'] and c['source_sha256']==h['source_sha256'] and norm(h['value']) in norm(c['text']) and metric_match(h['metric'],c['text'])]
            hits.append(bool(matching));cited.append(bool(set(matching)&citations))
        bridge_order=None
        if row['type']=='bridge':
            hmap={h['hop_id']:h for h in row['hops']};d=row['derivation'];selector_companies={hmap[k]['company'] for k in d['selection_hop_ids']};target=hmap[d['target_hop_id']]
            seen_selectors=set();bridge_order=False;pending=None
            for e in trajectory:
                if e['event']=='tool_request' and e['tool']=='search':
                    args=e['args'];pending=args
                    keyword='现金流' if '现金' in target['metric'] else '净利润'
                    if args['company']==target['company'] and keyword in args['query'] and selector_companies.issubset(seen_selectors):bridge_order=True
                if e['event']=='tool_response' and e['tool']=='search' and pending:
                    if '营业收入' in pending['query'] and e['response']['results']:seen_selectors.add(pending['company'])
        for u in r['usage']:total_usage.update({k:v for k,v in u.items() if isinstance(v,int)})
        results.append({'id':row['id'],'type':row['type'],'status':'ERROR' if r['error'] else final['status'] if final else 'INCOMPLETE','correct':correct,'search_calls':r['search_calls'],'decision_calls':r['decision_calls'],'hop_anchor_hits':hits,'cited_hop_anchor_hits':cited,'target_search_after_selector_responses':bridge_order,'error':r['error'],'answer':final})
    report={'scope':'12-question developmental integration pilot, not M3 dev20 acceptance or independent test','correct':sum(r['correct'] for r in results),'total':len(gold),'results':results,'token_usage':dict(total_usage),'limitations':['anchor matching is a diagnostic proxy, not semantic faithfulness','conditional bridges only; target_search ordering is observable behavior, not proof of internal reasoning','native Codex runtime plus external action loop, not trained student or LangGraph four-role graph'],'artifacts':{'gold_sha256':frozen['gold_sha256'],'questions_sha256':frozen['questions_sha256']}}
    a.out.write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps({'correct':report['correct'],'total':report['total'],'usage':report['token_usage']}))

if __name__=='__main__':main()
