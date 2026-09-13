"""Cross-check journal decisions against actual Codex events and observed evidence."""
import argparse
import hashlib
import json
from pathlib import Path


def audit_case(folder):
    rows=list(map(json.loads,(folder/'trajectory.jsonl').read_text().splitlines()))
    pending=None;searches=0;calculations=0;native_items={};usage={};seen=set();final=None
    for row in rows:
        if row['event']=='teacher_decision':
            step=folder/f"step_{row['step']:02}"
            events=list(map(json.loads,(step/'events.jsonl').read_text().splitlines()))
            completed=[e['item']['text'] for e in events if e['type']=='item.completed' and e['item']['type']=='agent_message']
            if not completed or json.loads(completed[-1])!=row['action']:raise ValueError('journal differs from actual teacher response')
            turns=[e for e in events if e['type']=='turn.completed']
            if len(turns)!=1 or turns[0]['usage']!=row['usage']:raise ValueError('missing/mismatched usage')
            for event in events:
                if event['type'] in ('item.started','item.completed'):
                    kind=event['item']['type'];native_items[kind]=native_items.get(kind,0)+1
                    if kind not in ('agent_message','reasoning','error'):raise ValueError('native tool outside experiment')
            for k,v in row['usage'].items():usage[k]=usage.get(k,0)+v
        elif row['event']=='tool_request':
            if pending:raise ValueError('unanswered request')
            pending=row
        elif row['event']=='tool_response':
            if not pending or pending['tool']!=row['tool'] or pending['step']!=row['step']:raise ValueError('orphan/mismatched tool response')
            if row['tool']=='search':
                searches+=1
                if 'error' not in row['response'] and 'results' not in row['response']:
                    raise ValueError('search response has neither results nor error')
                for chunk in row['response'].get('results',[]):seen.add(chunk['chunk_id'])
            elif row['tool']=='calculate':calculations+=1
            pending=None
        elif row['event']=='final':
            final=row['response']
            if not set(final['citations']).issubset(seen):raise ValueError('unobserved citation')
    result=json.loads((folder/'result.json').read_text())
    if result['final']!=final:raise ValueError('result differs from journal')
    if not result['error'] and (pending or final is None):raise ValueError('incomplete successful journal')
    return {'id':folder.name,'status':'AUDIT_PASSED','search_responses':searches,'calculation_responses':calculations,'native_items':native_items,'usage':usage,'run_error':result['error'],'trajectory_sha256':hashlib.sha256((folder/'trajectory.jsonl').read_bytes()).hexdigest()}

def main():
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    if a.out.exists():raise FileExistsError(a.out)
    results=[]
    for folder in sorted(a.run.glob('bridge-pilot-*')):
        try:results.append(audit_case(folder))
        except Exception as e:results.append({'id':folder.name,'status':'AUDIT_FAILED','error':str(e)})
    report={'results':results,'passed':sum(r['status']=='AUDIT_PASSED' for r in results),'total':len(results),'scope':'consistency with stored actual teacher events and HTTP journal, not an independent semantic judge'}
    a.out.write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps({'passed':report['passed'],'total':report['total']}))
    raise SystemExit(0 if report['passed']==report['total'] else 1)

if __name__=='__main__':main()
