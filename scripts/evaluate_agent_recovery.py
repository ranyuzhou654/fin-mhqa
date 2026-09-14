"""Offline outcomes for five synthetic fault cases; separate from dev20."""
import argparse
from decimal import Decimal
import json
from pathlib import Path
from audit_agent_run import audit_agent_case


def main():
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);p.add_argument('--gold',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    if a.out.exists():raise FileExistsError(a.out)
    manifest=json.loads((a.run/'manifest.json').read_text())
    gold=next(json.loads(l) for l in a.gold.read_text().splitlines() if json.loads(l)['id']==manifest['question_id'])
    expected_company=next(h['company'] for h in gold['hops'] if h['hop_id']==gold['derivation']['target_hop_id'])
    context=None
    if manifest.get('context_index'):
        from finmhqa.agent.evidence_context import EvidenceContext
        context=EvidenceContext(manifest['context_index'],manifest['health']['admission']['dataset_sha256'])
    rows=[]
    for scenario in manifest['scenarios']:
        folder=a.run/scenario
        if not (folder/'result.json').exists():rows.append({'id':scenario,'status':'NOT_RUN','correct':False});continue
        result=json.loads((folder/'result.json').read_text());final=result['final']
        try:correct=bool(result['injection_applied'] and not result['error'] and final and final['status']=='answered' and Decimal(final['answer_value'])==Decimal(gold['answer_value']) and final['answer_unit']==gold['answer_unit'] and final['selected_company']==expected_company)
        except Exception:correct=False
        es=[json.loads(l) for l in (folder/'trajectory.jsonl').read_text().splitlines()]
        try:audit=audit_agent_case(folder,context.read if context else None)
        except Exception as e:audit={'status':'AUDIT_FAILED','error':str(e)}
        original_http=sum(e['event']=='tool_response' and e['tool']=='search' and 'error' not in e['response'] for e in es)
        rows.append({**result,'correct':correct,'audit':audit,'successful_underlying_http_responses':original_http,
            'teacher_acknowledged_conflict':any(e['event']=='teacher_decision' and '冲突' in json.dumps(e['action'],ensure_ascii=False) for e in es),
            'planner_feedback':[e['feedback'] for e in es if e['event']=='planner_feedback'],
            'queries':[e['args'] for e in es if e['event']=='tool_request' and e['tool']=='search']})
    report={'scope':'declared fault injections on one development question; not independent robustness benchmark or normal HTTP retrieval scores','correct':sum(r['correct'] for r in rows),'total':len(manifest['scenarios']),'results':rows,
        'limitations':['conflict case explicitly signals synthetic conflict; not blind detection','unit/period masking only affects first query; later genuine evidence may recover without verifier rejection','numeric correctness does not itself prove the injected ambiguity was resolved']}
    a.out.write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps({'correct':report['correct'],'total':report['total']}))

if __name__=='__main__':main()
