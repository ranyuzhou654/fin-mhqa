"""Run frozen question-only cases; never opens gold. All arms share tools/budgets."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import threading
import time
from finmhqa.agent.codex_teacher import CodexTeacher
from finmhqa.agent.graph import AgentGraph
from finmhqa.agent.pilot_tools import request_json,search


def main():
    p=argparse.ArgumentParser();p.add_argument('--questions',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--arms',nargs='+',choices=['structured','generic','flat'],default=['structured','generic','flat'])
    p.add_argument('--url',default='http://127.0.0.1:8100');p.add_argument('--workers',type=int,choices=[1,2,4],default=2)
    p.add_argument('--only');a=p.parse_args()
    if a.out.exists():raise FileExistsError(a.out)
    rows=[json.loads(l) for l in a.questions.read_text().splitlines()]
    if any(set(r)!={'id','question'} for r in rows) or len({r['id'] for r in rows})!=len(rows):raise ValueError('unique question-only input required')
    if a.only:rows=[r for r in rows if r['id']==a.only]
    if not rows:raise ValueError('no cases')
    a.out.mkdir(parents=True)
    teacher=CodexTeacher('/Applications/ChatGPT.app/Contents/Resources/codex',a.out/'empty_teacher_cwd')
    health=request_json(a.url+'/health')
    if health['count']!=9322 or health['admission']['status']!='ACCEPTED':raise ValueError('wrong corpus/admission')
    files=[Path('src/finmhqa/agent')/f for f in ['codex_teacher.py','graph.py','graph_contracts.py','pilot_tools.py']]+[Path(__file__)]
    metadata={'questions_sha256':hashlib.sha256(a.questions.read_bytes()).hexdigest(),'teacher':'gpt-5.6-sol',
        'langgraph':importlib.metadata.version('langgraph'),'health_before':health,'arms':a.arms,'workers':a.workers,
        'max_models':10,'max_tools':8,'max_searches':6,'max_replans':2,'code_sha256':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
        'scope':'development only; authored from previously reviewed facts; not independent Test',
        'flat':'existing action policy with shared safety guards and budgets; no semantic verifier or synthesis model call',
        'generic':'same graph and verifier; only failure feedback to planner replaced with generic failure message'}
    (a.out/'manifest.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2))
    lock=threading.Lock()
    def run(task):
        arm,row=task;folder=a.out/arm/row['id'];folder.mkdir(parents=True)
        logs=[];start=time.monotonic();state={};error=None
        with (folder/'trajectory.jsonl').open('x') as f:
            def record(e):
                logs.append(e);f.write(json.dumps(e,ensure_ascii=False)+'\n');f.flush();os.fsync(f.fileno())
            record({'event':'question',**row,'arm':arm})
            def model(question,history,step,prompt,schema):
                return teacher.decide(question,history,folder/f'step_{step:02}',system=prompt,output_schema=schema)
            def retrieve(args):
                with lock:return search(a.url,args,health['admission']['dataset_sha256'])
            try:state=AgentGraph(model,retrieve,record,arm).run(row['question'])
            except Exception as exc:error=f'{type(exc).__name__}: {exc}';record({'event':'error','message':error})
        result={'id':row['id'],'arm':arm,'final':state.get('final'),'error':error,'terminal_reason':state.get('terminal_reason','ERROR'),
            'seconds':time.monotonic()-start,'search_calls':sum(e['event']=='tool_request' and e['tool']=='search' for e in logs),
            'tool_calls':sum(e['event']=='tool_request' for e in logs),'decision_calls':sum(e['event']=='teacher_decision' for e in logs),
            'replan_count':state.get('replan_count'),'usage':[e['usage'] for e in logs if e['event']=='teacher_decision']}
        (folder/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        print(json.dumps({k:result[k] for k in ['id','arm','terminal_reason','search_calls','decision_calls','error']},ensure_ascii=False),flush=True)
    # Rotate order by question to reduce fixed arm/time ordering effects; not a randomised statistical trial.
    tasks=[(a.arms[(i+j)%len(a.arms)],row) for i,row in enumerate(rows) for j in range(len(a.arms))]
    with ThreadPoolExecutor(max_workers=a.workers) as pool:list(pool.map(run,tasks))
    after=request_json(a.url+'/health')
    if after!=health:raise ValueError('index changed')
    (a.out/'health_after.json').write_text(json.dumps(after,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
