"""Five declared fault-injection runs. Never modify source corpus or main-run data."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
from finmhqa.agent.codex_teacher import CodexTeacher
from finmhqa.agent.graph import AgentGraph
from finmhqa.agent.pilot_tools import request_json,search

SCENARIOS=['empty_first','timeout_first','unit_mask_first','period_mask_first','conflict_first']

def transform(response,scenario):
    response=deepcopy(response)
    if scenario=='empty_first':response['results']=[]
    elif scenario in ('unit_mask_first','period_mask_first'):
        for c in response['results']:
            if scenario=='unit_mask_first':
                c['text']=re.sub(r'亿元|千元|万元|人民币元|元','[单位遮蔽]',c['text']);c['unit']=''
            else:
                c['text']=re.sub(r'2025|2024|本报告期|本期|上年同期|上期|半年度|上半年','[期间遮蔽]',c['text']);c['report_period']=''
    elif scenario=='conflict_first':
        # A visibly incompatible same-scope numerical claim is synthetic; its true source is recorded outside model input.
        if response['results']:
            c=deepcopy(response['results'][0]);c['chunk_id']='fault-conflict-001'
            c['text']='【美的集团 | 2025年半年度报告 | 合并利润表】单位：千元。2025年上半年合并营业收入：99,999,999千元。该值与其他返回表格存在冲突，需进一步核对。'
            response['results'].append(c)
    return response


def main():
    p=argparse.ArgumentParser();p.add_argument('--questions',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--url',default='http://127.0.0.1:8100');a=p.parse_args()
    if a.out.exists():raise FileExistsError(a.out)
    rows=[json.loads(l) for l in a.questions.read_text().splitlines()]
    if any(set(r)!={'id','question'} for r in rows):raise ValueError('question-only input required')
    question=next(r['question'] for r in rows if r['id']=='agent-dev-001')
    a.out.mkdir(parents=True);health=request_json(a.url+'/health');lock=threading.Lock()
    teacher=CodexTeacher('/Applications/ChatGPT.app/Contents/Resources/codex',a.out/'empty_teacher_cwd')
    manifest={'scope':'fault injection only; not natural retrieval or dev20 correctness','scenarios':SCENARIOS,
        'question_id':'agent-dev-001','question':question,'health':health,'teacher':'gpt-5.6-sol',
        'code_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'conflict_caveat':'synthetic alternative explicitly signals a conflict; demonstrates prompted conflict handling, not blind detection'}
    (a.out/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    def run(scenario):
        folder=a.out/scenario;folder.mkdir();logs=[];state={};error=None;used=False;start=time.monotonic()
        with (folder/'trajectory.jsonl').open('x') as log:
            def record(e):logs.append(e);log.write(json.dumps(e,ensure_ascii=False)+'\n');log.flush();os.fsync(log.fileno())
            record({'event':'question','id':scenario,'question':question,'scope':'FAULT_INJECTION'})
            def retrieve(args):
                nonlocal used
                inject=not used and args['company']=='美的集团'
                if inject:used=True
                if inject and scenario=='timeout_first':
                    record({'event':'fault_injection','scenario':scenario,'real_http_executed':False});raise TimeoutError('injected transport timeout')
                with lock:response=search(a.url,args,health['admission']['dataset_sha256'])
                if inject:
                    record({'event':'fault_injection','scenario':scenario,'real_http_executed':True,'original_response':response})
                    response=transform(response,scenario)
                return response
            def model(q,payload,step,prompt,schema):return teacher.decide(q,payload,folder/f'step_{step:02}',system=prompt,output_schema=schema)
            try:state=AgentGraph(model,retrieve,record).run(question)
            except Exception as exc:error=f'{type(exc).__name__}: {exc}';record({'event':'error','message':error})
        result={'id':scenario,'final':state.get('final'),'error':error,'terminal_reason':state.get('terminal_reason','ERROR'),
            'replan_count':state.get('replan_count'),'injection_applied':used,'seconds':time.monotonic()-start,
            'search_calls':sum(e['event']=='tool_request' and e['tool']=='search' for e in logs),
            'usage':[e['usage'] for e in logs if e['event']=='teacher_decision']}
        (folder/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result,ensure_ascii=False),flush=True)
    with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(run,SCENARIOS))

if __name__=='__main__':main()
