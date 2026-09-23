"""Audit actual model/tool correspondence, calculation replay and frozen executable files."""
from collections import Counter
import hashlib
import json
from pathlib import Path
from finmhqa.agent.pilot_tools import calculate
from audit_teacher_trajectory import audit_case


def audit_agent_case(folder,context_reader=None,limits=(10,8,6)):
    base=audit_case(folder)
    events=[json.loads(l) for l in (folder/'trajectory.jsonl').read_text().splitlines()]
    result=json.loads((folder/'result.json').read_text());policy=None;pending=None;remaining=[];models=0;tools=0;searches=0;model_final=None;tried=[];seen={};context_reads=0
    for e in events:
        if e['event']=='teacher_decision':
            if e['step']!=models:raise ValueError('nonsequential model step')
            models+=1
            if e['role']=='planner':
                policy=e['action'];remaining=list(policy['searches'] or []) if policy['action']=='search' else []
            elif e['role']=='synthesizer':model_final=e['action']
        elif e['event']=='tool_request':
            tools+=1;pending=e
            if not policy or policy['action']!=e['tool']:raise ValueError('tool not requested by policy')
            if e['tool']=='search':
                searches+=1
                while remaining and remaining[0] in tried:remaining.pop(0)
                if not remaining or e['args']!=remaining.pop(0):raise ValueError('search args differ from model')
                tried.append(e['args'])
            elif e['tool']=='read_evidence_context':
                if e['args']!=policy.get('context'):raise ValueError('context args differ from model')
            elif e['args']!=policy['calculation']:raise ValueError('calculation args differ from model')
        elif e['event']=='tool_response':
            if e['tool']=='read_evidence_context' and 'error' not in e['response']:
                anchor=seen.get(pending['args']['anchor_id'])
                if anchor is None:raise ValueError('unobserved context anchor')
                if any(any(c[k]!=anchor[k] for k in ('company','source_sha256')) for c in e['response']['results']):
                    raise ValueError('context escaped document')
                if context_reader is not None and context_reader(pending['args'])!=e['response']:
                    raise ValueError('context replay mismatch')
                context_reads+=1
            if e['tool'] in ('search','read_evidence_context'):
                seen.update({c['chunk_id']:c for c in e['response'].get('results',[])})
            if e['tool']=='calculate' and 'error' not in e['response'] and calculate(pending['args'])!=e['response']:raise ValueError('calculation replay mismatch')
            pending=None
        elif e['event']=='final' and e['response']['status']=='answered':
            expected=model_final if model_final is not None else policy.get('final') if policy else None
            if e['response']!=expected:raise ValueError('published answer differs from actual teacher candidate/output')
    if models!=result['decision_calls'] or tools!=result['tool_calls'] or searches!=result['search_calls']:raise ValueError('result counts mismatch')
    if models>limits[0] or tools>limits[1] or searches>limits[2]:raise ValueError('budget exceeded')
    return {**base,'policy_tool_correspondence':'PASSED','calculation_replay':'PASSED','context_reads':context_reads,'context_replay':'PASSED' if context_reader is not None else 'NOT_REQUESTED'}


def audit_run(run):
    manifest=json.loads((run/'manifest.json').read_text());results=[]
    context=None
    if manifest.get('context_index'):
        from finmhqa.agent.evidence_context import EvidenceContext
        context=EvidenceContext(manifest['context_index'],manifest['health_before']['admission']['dataset_sha256'],manifest.get('context_version',1))
    limits=(manifest.get('max_models',10),manifest.get('max_tools',8),manifest.get('max_searches',6))
    for arm in manifest['arms']:
        for case in sorted(c for c in (run/arm).iterdir() if (c/'trajectory.jsonl').exists()):
            try:report=audit_agent_case(case,context.read if context else None,limits)
            except Exception as e:report={'id':case.name,'status':'AUDIT_FAILED','error':str(e)}
            results.append({'arm':arm,**report})
    return {'results':results,'passed':sum(r['status']=='AUDIT_PASSED' for r in results),'total':len(results),
            'current_execution_code_matches_run':{p:Path(p).exists() and hashlib.sha256(Path(p).read_bytes()).hexdigest()==sha for p,sha in manifest['code_sha256'].items()}}

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    if a.out.exists():raise FileExistsError(a.out)
    r=audit_run(a.run);a.out.write_text(json.dumps(r,ensure_ascii=False,indent=2));print(json.dumps({'passed':r['passed'],'total':r['total']}))
