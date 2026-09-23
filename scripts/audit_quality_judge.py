"""Audit stored Sol inputs, unique sessions, raw decisions and content-bound labels."""
from collections import Counter
import argparse
import hashlib
import json
from pathlib import Path
from finmhqa.qa.judge import bind,schema_for,system_prompt
from finmhqa.qa.calibration import digest
from finmhqa.agent.openai_compat import normalized_events
from score_quality_calibration import load_packet


def audit_case(case,folder,rubric,reviewer='gpt-5.6-sol (Codex, reasoning=low)',backend='codex'):
    r=json.loads((folder/'result.json').read_text());call=folder/'call'
    if r['id']!=case['id'] or r['case_sha256']!=digest(case):raise ValueError('wrong case binding')
    expected=system_prompt(rubric,backend)+'\n'+json.dumps({'question':case['question'],'history':case},ensure_ascii=False)
    if (call/'input.txt').read_text()!=expected:raise ValueError('input differs from frozen case/rubric')
    if json.loads((call/'schema.json').read_text())!=schema_for(case):raise ValueError('schema changed')
    events=[json.loads(l) for l in (call/'events.jsonl').read_text().splitlines()]
    if (call/'response.json').exists() and normalized_events(json.loads((call/'response.json').read_text()))!=events:
        raise ValueError('normalized events differ from raw API response')
    sessions=[e['thread_id'] for e in events if e.get('type')=='thread.started']
    if len(sessions)!=1:raise ValueError('expected one fresh session')
    native_items=Counter();messages=[];usage=[];notices=[]
    for e in events:
        if e.get('type') in ('item.started','item.completed'):
            item=e['item'];kind=item['type'];native_items[kind]+=1
            if kind not in ('agent_message','reasoning','error'):raise ValueError('unapproved native tool item')
            if e['type']=='item.completed' and kind=='agent_message':messages.append(item['text'])
            if kind=='error':notices.append(item)
        if e.get('type')=='turn.completed':usage.append(e['usage'])
    if r['error']:
        return {'id':case['id'],'status':'RUN_FAILED','session_id':sessions[0],'error':r['error'],'native_notices':notices}
    if len(usage)!=1 or usage[0]!=r['usage'] or not messages:raise ValueError('missing model output/usage')
    decision=json.loads(messages[-1])
    if decision!=json.loads((call/'decision.json').read_text()):raise ValueError('saved decision differs from native event')
    if usage[0]!=json.loads((call/'usage.json').read_text()):raise ValueError('saved usage differs')
    if bind(case,decision,r['review']['reviewed_at'],reviewer)!=r['review']:raise ValueError('published labels differ from actual model')
    return {'id':case['id'],'status':'AUDIT_PASSED','session_id':sessions[0],'usage':r['usage'],'native_items':dict(native_items),'native_notices':notices}


def audit_run(packet,run):
    cases,_=load_packet(packet);manifest=json.loads((run/'manifest.json').read_text())
    if manifest['packet_sha256']!=hashlib.sha256((packet/'manifest.json').read_bytes()).hexdigest():raise ValueError('packet changed')
    if manifest['case_ids']!=[c['id'] for c in cases] or manifest['case_sha256']!={c['id']:digest(c) for c in cases}:raise ValueError('run set changed')
    rubric=json.loads((packet/'rubric.json').read_text())['prompt'];results=[]
    for c in cases:
        try:results.append(audit_case(c,run/c['id'],rubric,manifest.get('reviewer','gpt-5.6-sol (Codex, reasoning=low)'),manifest.get('backend','codex')))
        except Exception as e:results.append({'id':c['id'],'status':'AUDIT_FAILED','error':str(e)})
    sessions=[r['session_id'] for r in results if 'session_id' in r]
    if len(sessions)!=len(set(sessions)):raise ValueError('Judge sessions reused across cases')
    reviews=[json.loads((run/c['id']/'result.json').read_text())['review'] for c in cases]
    if json.loads((run/'predictions.json').read_text())!=reviews:raise ValueError('aggregate predictions changed')
    matches={p:Path(p).exists() and hashlib.sha256(Path(p).read_bytes()).hexdigest()==sha for p,sha in manifest['code_sha256'].items()}
    return {'scope':'input/event/schema/provenance consistency; not semantic correctness','total':len(cases),
            'passed':sum(r['status']=='AUDIT_PASSED' for r in results),'unique_sessions':len(set(sessions)),
            'current_execution_matches':matches,'results':results}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--packet',type=Path,required=True);p.add_argument('--run',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    if a.out.exists():raise FileExistsError(a.out)
    r=audit_run(a.packet,a.run);a.out.write_text(json.dumps(r,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'passed':r['passed'],'total':r['total'],'unique_sessions':r['unique_sessions']}))
