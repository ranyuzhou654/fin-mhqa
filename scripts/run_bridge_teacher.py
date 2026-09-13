"""Question-only teacher execution. Gold is not opened by this process."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from finmhqa.agent.codex_teacher import CodexTeacher, SYSTEM, SCHEMA
from finmhqa.agent.pilot_tools import calculate, request_json, search

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--questions',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--binary',default='/Applications/ChatGPT.app/Contents/Resources/codex')
    p.add_argument('--url',default='http://127.0.0.1:8100')
    p.add_argument('--only',default=None)
    p.add_argument('--workers',type=int,choices=[1,2],default=1)
    a=p.parse_args()
    if a.out.exists():raise FileExistsError(a.out)
    a.out.mkdir(parents=True)
    rows=list(map(json.loads,a.questions.read_text().splitlines()))
    if any(set(r)!={'id','question'} for r in rows):raise ValueError('question-only input required')
    if a.only:rows=[r for r in rows if r['id']==a.only]
    if not rows:raise ValueError('empty question selection')
    teacher=CodexTeacher(a.binary,a.out/'empty_teacher_cwd')
    health=request_json(a.url+'/health')
    if health['count']!=9322 or health['admission']['status']!='ACCEPTED':raise ValueError('wrong index')
    dataset=health['admission']['dataset_sha256']
    metadata={'teacher':'gpt-5.6-sol','backend':'Codex CLI 0.153.4 ChatGPT login','questions_sha256':hashlib.sha256(a.questions.read_bytes()).hexdigest(),'system_prompt_sha256':hashlib.sha256(SYSTEM.encode()).hexdigest(),'health_before':health,'max_searches':6,'max_decisions':10,'workers':a.workers,'schema':SCHEMA,'architecture':'bounded external action loop; not full LangGraph','native_tools':'disabled where supported; any emitted native tool item rejects the run'}
    (a.out/'manifest.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2))
    retrieval_lock=threading.Lock()
    def run_case(row):
        case=a.out/row['id'];case.mkdir();history=[];seen=set();calls=0;calculated=False;final=None;error=None;usage=[]
        with (case/'trajectory.jsonl').open('x') as log:
            def record(event):
                log.write(json.dumps(event,ensure_ascii=False)+'\n');log.flush();os.fsync(log.fileno())
            record({'event':'question','id':row['id'],'question':row['question']})
            try:
                for step in range(10):
                    before=time.monotonic()
                    action,tokens=teacher.decide(row['question'],history,case/f'step_{step:02}')
                    usage.append(tokens)
                    record({'event':'teacher_decision','step':step,'action':action,'usage':tokens,'seconds':time.monotonic()-before})
                    observations=[]
                    if action['action']=='search':
                        requests=action['searches']
                        if not requests or not 1<=len(requests)<=2 or calls+len(requests)>6:raise ValueError('search budget/arity violation')
                        for args in requests:
                            calls+=1;start=time.monotonic()
                            record({'event':'tool_request','step':step,'tool':'search','args':args})
                            with retrieval_lock:
                                response=search(a.url,args,dataset)
                            record({'event':'tool_response','step':step,'tool':'search','response':response,'seconds':time.monotonic()-start})
                            for c in response['results']:seen.add(c['chunk_id'])
                            observations.append({'tool':'search','args':args,'response':response})
                    elif action['action']=='calculate':
                        record({'event':'tool_request','step':step,'tool':'calculate','args':action['calculation']})
                        try:
                            result=calculate(action['calculation']);calculated=True
                        except (ValueError, KeyError, ArithmeticError, TypeError) as exc:
                            result={'error':'INVALID_CALCULATION','detail':str(exc),'recoverable':True}
                        record({'event':'tool_response','step':step,'tool':'calculate','response':result})
                        observations.append({'tool':'calculate','response':result})
                    elif action['action']=='finish':
                        final=action['final']
                        if not final:raise ValueError('missing final')
                        if final['status']=='answered':
                            if not calculated:raise ValueError('answered without calculation')
                            if not final['citations'] or not set(final['citations']).issubset(seen):raise ValueError('unseen/missing citation')
                        record({'event':'final','response':final});break
                    else:raise ValueError('unsupported teacher action')
                    history.append({'action':action,'observations':observations})
                else:raise ValueError('decision budget exceeded')
            except Exception as exc:
                error=f'{type(exc).__name__}: {exc}';record({'event':'error','message':error})
        result={'id':row['id'],'final':final,'error':error,'search_calls':calls,'decision_calls':len(usage),'usage':usage,'seen_chunk_ids':sorted(seen)}
        (case/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        print(json.dumps({'id':row['id'],'status':'ERROR' if error else final['status'],'search_calls':calls,'error':error},ensure_ascii=False),flush=True)
    with ThreadPoolExecutor(max_workers=a.workers) as pool:
        list(pool.map(run_case,rows))
    after=request_json(a.url+'/health')
    if after!=health:raise ValueError('health changed during evaluation')
    (a.out/'health_after.json').write_text(json.dumps(after,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
