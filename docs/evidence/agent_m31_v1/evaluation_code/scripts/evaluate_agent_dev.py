"""Offline dev20 comparison; errors and missing runs stay in all denominators."""
import argparse
from collections import Counter
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import statistics
from audit_teacher_trajectory import audit_case
from evaluate_bridge_teacher import norm,metric_match


def score_case(row,expected,folder):
    if not (folder/'result.json').exists():return {'id':row['id'],'correct':False,'status':'NOT_RUN','audit':'MISSING'}
    r=json.loads((folder/'result.json').read_text());events=[json.loads(l) for l in (folder/'trajectory.jsonl').read_text().splitlines()]
    final=r['final'];correct=False
    if not r['error'] and final and final['status']=='answered':
        try:correct=Decimal(final['answer_value'])==Decimal(row['answer_value']) and final['answer_unit']==row['answer_unit']
        except Exception:pass
        if row['type']=='bridge':correct=correct and final['selected_company']==expected['selected_company']
    chunks={c['chunk_id']:c for e in events if e['event']=='tool_response' and e['tool'] in ('search','read_evidence_context') for c in e['response'].get('results',[])}
    citations=set(final['citations']) if final else set();hits=[];cited=[]
    for h in row['hops']:
        match={cid for cid,c in chunks.items() if c['company_code']==h['company_code'] and c['source_sha256']==h['source_sha256'] and norm(h['value']) in norm(c['text']) and metric_match(h['metric'],c['text'])}
        hits.append(bool(match));cited.append(bool(match&citations))
    try:audit=audit_case(folder)
    except Exception as e:audit={'status':'AUDIT_FAILED','error':str(e)}
    return {**r,'correct':bool(correct),'audit':audit,'hop_anchor_hits':hits,'cited_hop_anchor_hits':cited,
            'semantic_verifier_rejections':sum(e['event']=='teacher_decision' and e.get('role')=='verifier' and not e['action']['sufficient'] for e in events),
            'recoverable_tool_errors':sum(e['event']=='tool_response' and 'error' in e['response'] for e in events),
            'status':'ERROR' if r['error'] else final['status'] if final else 'INCOMPLETE'}


def main():
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);p.add_argument('--gold',type=Path,required=True)
    p.add_argument('--freeze',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    if a.out.exists():raise FileExistsError(a.out)
    gold=[json.loads(l) for l in a.gold.read_text().splitlines()];freeze=json.loads(a.freeze.read_text());manifest=json.loads((a.run/'manifest.json').read_text())
    if hashlib.sha256(a.gold.read_bytes()).hexdigest()!=freeze['gold_sha256'] or manifest['questions_sha256']!=freeze['questions_sha256']:raise ValueError('frozen input mismatch')
    if len(gold)!=20:raise ValueError('must score fixed dev20 denominator')
    expected={r['id']:r for r in freeze['results']};arms={}
    for arm in manifest['arms']:
        results=[score_case(r,expected[r['id']],a.run/arm/r['id']) for r in gold]
        done=[r for r in results if r['status']!='NOT_RUN'];usage=Counter()
        for r in done:
            for u in r['usage']:usage.update(u)
        correct=sum(r['correct'] for r in results);errors=sum(r['status']=='ERROR' for r in results)
        tools=sum(r['tool_calls'] for r in done);audits=sum(r['audit']['status']=='AUDIT_PASSED' for r in done)
        arms[arm]={'correct':correct,'total':20,'completed':len(done),'errors':errors,'tool_calls':tools,'mean_tools':tools/20,
            'search_calls':sum(r['search_calls'] for r in done),'model_calls':sum(r['decision_calls'] for r in done),
            'mean_seconds':statistics.mean(r['seconds'] for r in done) if done else None,
            'p95_seconds':sorted(r['seconds'] for r in done)[max(0,__import__('math').ceil(.95*len(done))-1)] if done else None,
            'audits_passed':audits,'token_usage':dict(usage),'semantic_verifier_rejections':sum(r['semantic_verifier_rejections'] for r in done),
            'runtime_gate':'PASSED' if len(done)==20 and correct>=8 and tools/20<=6 and not errors and audits==20 else 'FAILED_OR_INCOMPLETE',
            'replan_observation':'OBSERVED' if any((r.get('replan_count') or 0)>0 for r in done) else 'NOT_OBSERVED',
            'results':results}
    pairs=[]
    if {'structured','generic','flat'}.issubset(arms):
        for i,r in enumerate(gold):pairs.append({'id':r['id'],**{arm:arms[arm]['results'][i]['correct'] for arm in arms}})
    report={'scope':'assistant-authored, overlapping source facts, single-run Dev; not independent Test or independent Faithfulness',
        'arms':arms,'paired_correctness':pairs,'limitations':['same-model verifier is not independent judge','string anchor misses equivalent units','single-run latency includes concurrent model load and serialized retrieval','no observed replans means feedback benefit is not established'],
        'gold_sha256':freeze['gold_sha256'],'questions_sha256':freeze['questions_sha256']}
    a.out.write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps({k:{n:v[n] for n in ['correct','completed','errors','mean_tools','runtime_gate']} for k,v in arms.items()}))

if __name__=='__main__':main()
