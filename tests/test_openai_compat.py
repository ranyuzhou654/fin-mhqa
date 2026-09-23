import json
from pathlib import Path
import sys
import pytest
from finmhqa.agent.openai_compat import OpenAICompatTeacher, normalized_events, load_env
from finmhqa.agent.codex_teacher import SCHEMA
sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))

ACTION={'action':'search','reason':'r','searches':[{'query':'q','company':'甲','mode':'hybrid'}],'calculation':None,'final':None}

def reply(content,rid='resp-1',**message):
    return {'id':rid,'choices':[{'message':{'role':'assistant','content':content,**message}}],
            'usage':{'prompt_tokens':100,'completion_tokens':20,'prompt_tokens_details':{'cached_tokens':40},'completion_tokens_details':{'reasoning_tokens':5}}}

def teacher(replies,sent=None):
    replies=iter(replies)
    def post(body,timeout):
        if sent is not None:sent.append(body)
        return next(replies)
    return OpenAICompatTeacher('https://example.test/v1','m',api_key='sk-secret-value',post=post)


def test_valid_reply_is_normalised_and_key_never_written(tmp_path):
    sent=[]
    action,usage=teacher([reply(json.dumps(ACTION),reasoning_content='思考')],sent).decide('问',[],tmp_path/'s',system='SYS',output_schema=SCHEMA)
    assert action==ACTION and usage=={'input_tokens':100,'cached_input_tokens':40,'output_tokens':20,'reasoning_output_tokens':5}
    assert 'tools' not in sent[0] and sent[0]['response_format']=={'type':'json_object'}
    assert all('sk-secret-value' not in f.read_text() for f in (tmp_path/'s').rglob('*') if f.is_file())
    events=[json.loads(l) for l in (tmp_path/'s'/'events.jsonl').read_text().splitlines()]
    assert events==normalized_events(json.loads((tmp_path/'s'/'response.json').read_text()))
    assert (tmp_path/'s'/'input.txt').read_text()=='SYS\n'+json.dumps({'question':'问','history':[]},ensure_ascii=False)


def test_code_fence_is_stripped(tmp_path):
    action,_=teacher([reply('```json\n'+json.dumps(ACTION)+'\n```')]).decide('问',[],tmp_path/'s',system='S',output_schema=SCHEMA)
    assert action==ACTION


def test_malformed_json_retried_once_and_all_attempts_kept(tmp_path):
    t=teacher([reply('不是JSON','bad'),reply(json.dumps(ACTION),'good')])
    action,_=t.decide('问',[],tmp_path/'s',system='S',output_schema=SCHEMA)
    root=tmp_path/'s'
    assert action==ACTION and json.loads((root/'response.json').read_text())['id']=='good'
    assert json.loads((root/'rejected_response.json').read_text())['id']=='bad'
    assert json.loads((root/'attempts.json').read_text())['rejected_usage'][0]['input_tokens']==100
    with pytest.raises(RuntimeError,match='invalid JSON'):
        teacher([reply('x'),reply('y')]).decide('问',[],tmp_path/'t',system='S',output_schema=SCHEMA)


def test_provider_tool_calls_refused(tmp_path):
    with pytest.raises(RuntimeError,match='tool_call'):
        teacher([reply('',tool_calls=[{'id':'t'}])]).decide('问',[],tmp_path/'s',system='S',output_schema=SCHEMA)


def test_missing_key_refused(monkeypatch,tmp_path):
    monkeypatch.delenv('LLM_API_KEY',raising=False);monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError,match='LLM_API_KEY'):OpenAICompatTeacher('https://example.test/v1','m')


def test_env_file_fills_only_unset_llm_values(monkeypatch,tmp_path):
    monkeypatch.delenv('LLM_API_KEY',raising=False);monkeypatch.setenv('LLM_MODEL_NAME','keep')
    f=tmp_path/'.env';f.write_text('LLM_API_KEY="k"\nLLM_MODEL_NAME=other\nOTHER=x\n')
    load_env(f)
    import os
    assert os.environ['LLM_API_KEY']=='k' and os.environ['LLM_MODEL_NAME']=='keep' and 'OTHER' not in os.environ


def test_trajectory_audit_detects_tampered_normalised_events(tmp_path):
    from audit_teacher_trajectory import audit_case
    from finmhqa.agent.graph import AgentGraph
    from finmhqa.agent.graph_contracts import PLAN_SCHEMA
    chunk={'chunk_id':'c1','company':'甲','text':'收入100元'}
    plan=[{'id':'q1','question':'收入','depends_on':[],'status':'pending','conclusion':'','evidence_ids':[]}]
    final={'status':'insufficient','answer_value':'','answer_unit':'','selected_company':None,'citations':[],'explanation':'不足'}
    replies=[reply(json.dumps({**ACTION,'plan':plan}),'a'),reply(json.dumps({**ACTION,'action':'finish','searches':None,'final':final,'plan':plan}),'b')]
    t=teacher(replies);folder=tmp_path/'case';folder.mkdir();log=[]
    def record(e):
        log.append(e)
        with (folder/'trajectory.jsonl').open('a') as f:f.write(json.dumps(e,ensure_ascii=False)+'\n')
    model=lambda q,h,step,prompt,schema:t.decide(q,h,folder/f'step_{step:02}',system=prompt,output_schema=schema)
    state=AgentGraph(model,lambda _:{'results':[chunk]},record,synthesis='verified').run('甲收入')
    (folder/'result.json').write_text(json.dumps({'final':state['final'],'error':None}))
    assert audit_case(folder)['status']=='AUDIT_PASSED'
    events=folder/'step_00'/'events.jsonl';lines=events.read_text().splitlines()
    events.write_text('\n'.join([lines[0].replace('"a"','"forged"'),*lines[1:]])+'\n')
    with pytest.raises(ValueError,match='raw API response'):audit_case(folder)


def test_transport_errors_retried_and_logged_but_client_errors_not(tmp_path):
    calls=[]
    def flaky(body,timeout):
        calls.append(1)
        if len(calls)==1:raise ConnectionResetError('Remote end closed connection')
        return reply(json.dumps(ACTION))
    t=OpenAICompatTeacher('https://example.test/v1','m',api_key='k',post=flaky,backoff=0)
    action,_=t.decide('问',[],tmp_path/'s',system='S',output_schema=SCHEMA)
    assert action==ACTION and len(calls)==2
    assert json.loads((tmp_path/'s'/'transport_errors.json').read_text())[0]['retried'] is True
    def forbidden(body,timeout):
        e=RuntimeError('HTTP 403');e.transient=False;raise e
    with pytest.raises(RuntimeError,match='transport failed'):
        OpenAICompatTeacher('https://example.test/v1','m',api_key='k',post=forbidden,backoff=0).decide('问',[],tmp_path/'t',system='S',output_schema=SCHEMA)
