"""Compare completed dev20 runs; do not equate context use with semantic quality."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


def main():
    p=argparse.ArgumentParser();p.add_argument('--baseline',type=Path,required=True);p.add_argument('--evaluation',type=Path,required=True)
    p.add_argument('--run',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    if a.out.exists():raise FileExistsError(a.out)
    baseline=json.loads(a.baseline.read_text());new=json.loads(a.evaluation.read_text())
    if (baseline['gold_sha256'],baseline['questions_sha256'])!=(new['gold_sha256'],new['questions_sha256']):raise ValueError('different frozen dev sets')
    old=baseline['arms']['structured'];current=new['arms']['structured']
    if any(r['completed']!=20 for r in (old,current)):raise ValueError('requires complete dev20 runs')
    rows=[];scopes=Counter()
    for case in sorted((a.run/'structured').glob('agent-dev-*')):
        es=[json.loads(l) for l in (case/'trajectory.jsonl').read_text().splitlines()]
        requests=[e for e in es if e['event']=='tool_request' and e['tool']=='read_evidence_context']
        responses=[e for e in es if e['event']=='tool_response' and e['tool']=='read_evidence_context']
        scopes.update(e['args']['scope'] for e in requests)
        ids={c['chunk_id'] for e in responses for c in e['response'].get('results',[])}
        final=next(e['response'] for e in reversed(es) if e['event']=='final')
        rows.append({'id':case.name,'context_calls':len(requests),'context_text_chars':sum(e['response'].get('text_chars',0) for e in responses),
            'context_errors':sum('error' in e['response'] for e in responses),
            'context_returned_ids_cited':sorted(ids & set(final['citations'])),
            'finalize_reservation_events':sum(e['event']=='budget_reservation' for e in es),
            'terminal_reason':next(e['terminal_reason'] for e in reversed(es) if e['event']=='final')})
    keys=['correct','total','tool_calls','mean_tools','model_calls','search_calls','mean_seconds','errors','runtime_gate']
    prior={r['id']:r['correct'] for r in old['results']}
    report={'scope':'single-run historical comparison; combined intervention, no causal attribution or independent Faithfulness',
        'baseline':{k:old[k] for k in keys},'current':{k:current[k] for k in keys},
        'changed_correctness':[{'id':r['id'],'before':prior[r['id']],'after':r['correct']} for r in current['results'] if r['correct']!=prior[r['id']]],
        'context_calls':sum(r['context_calls'] for r in rows),'context_scope_counts':dict(scopes),
        'cases_using_context':sum(r['context_calls']>0 for r in rows),
        'cases_citing_context_returned_ids':sum(bool(r['context_returned_ids_cited']) for r in rows),
        'context_citation_caveat':'IDs may also have appeared in search; citation intersection does not prove unique benefit or complete support',
        'results':rows,'source_sha256':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [a.baseline,a.evaluation,Path(__file__)]}}
    a.out.write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps({k:report[k] for k in ['context_calls','context_scope_counts','cases_using_context']},ensure_ascii=False))

if __name__=='__main__':main()
