import json
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
from audit_agent_run import audit_agent_case


def fixture(p):
    args={'operation':'convert','values':[{'value':'2','unit':'元'}],'round_digits':2,'output_unit':'元'}
    action={'action':'calculate','searches':None,'calculation':args};usage={'input_tokens':1}
    step=p/'step_00';step.mkdir()
    (step/'events.jsonl').write_text(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':json.dumps(action)}})+'\n'+json.dumps({'type':'turn.completed','usage':usage})+'\n')
    final={'status':'insufficient','citations':[]}
    es=[{'event':'teacher_decision','role':'planner','step':0,'action':action,'usage':usage},
        {'event':'tool_request','step':0,'tool':'calculate','args':args},
        {'event':'tool_response','step':0,'tool':'calculate','response':{'value':'2.00','unit':'元','operation':'convert'}},
        {'event':'final','response':final}]
    (p/'result.json').write_text(json.dumps({'final':final,'error':None,'decision_calls':1,'tool_calls':1,'search_calls':0}))
    return es


def save(p,es):(p/'trajectory.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in es))

def test_actual_calculation_replay(tmp_path):
    save(tmp_path,fixture(tmp_path));assert audit_agent_case(tmp_path)['calculation_replay']=='PASSED'


def test_tampered_tool_result_detected(tmp_path):
    es=fixture(tmp_path);es[2]['response']['value']='3.00';save(tmp_path,es)
    with pytest.raises(ValueError,match='replay'):audit_agent_case(tmp_path)


def test_tampered_tool_arguments_detected(tmp_path):
    es=fixture(tmp_path)
    # Deep copy to avoid changing the original model action along with request.
    es[1]['args']=dict(es[1]['args'],round_digits=1);save(tmp_path,es)
    with pytest.raises(ValueError,match='args differ'):audit_agent_case(tmp_path)


def test_published_explanation_tampering_detected(tmp_path):
    es=fixture(tmp_path)
    original={'status':'answered','answer_value':'2.00','answer_unit':'元','citations':[],'explanation':'original'}
    action={'action':'finish','searches':None,'final':original};usage={'input_tokens':1}
    step=tmp_path/'step_01';step.mkdir()
    (step/'events.jsonl').write_text(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':json.dumps(action)}})+'\n'+json.dumps({'type':'turn.completed','usage':usage})+'\n')
    changed=dict(original,explanation='invented after review')
    es=es[:-1]+[{'event':'teacher_decision','role':'planner','step':1,'action':action,'usage':usage},{'event':'final','response':changed}]
    save(tmp_path,es)
    (tmp_path/'result.json').write_text(json.dumps({'final':changed,'error':None,'decision_calls':2,'tool_calls':1,'search_calls':0}))
    with pytest.raises(ValueError,match='published answer differs'):audit_agent_case(tmp_path)


@pytest.mark.parametrize('previously_tried',[True,False])
def test_search_skip_only_allowed_for_previously_tried_request(tmp_path,previously_tried):
    a={'query':'a','company':'甲','mode':'hybrid'};b=dict(a,query='b');first=a if previously_tried else dict(a,query='c')
    es=[]
    for i,(requests,executed) in enumerate([([first],first),([a,b],b)]):
        action={'action':'search','searches':requests};usage={'input_tokens':1}
        step=tmp_path/f'step_{i:02}';step.mkdir()
        (step/'events.jsonl').write_text(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':json.dumps(action)}})+'\n'+json.dumps({'type':'turn.completed','usage':usage})+'\n')
        es += [{'event':'teacher_decision','role':'planner','step':i,'action':action,'usage':usage},
               {'event':'tool_request','step':i,'tool':'search','args':executed},
               {'event':'tool_response','step':i,'tool':'search','response':{'results':[]}}]
    final={'status':'insufficient','citations':[]};es.append({'event':'final','response':final});save(tmp_path,es)
    (tmp_path/'result.json').write_text(json.dumps({'final':final,'error':None,'decision_calls':2,'tool_calls':2,'search_calls':2}))
    if previously_tried:assert audit_agent_case(tmp_path)['status']=='AUDIT_PASSED'
    else:
        with pytest.raises(ValueError,match='search args differ'):audit_agent_case(tmp_path)


@pytest.mark.parametrize('valid_error',[True,False])
def test_recoverable_search_error_is_a_paired_response(tmp_path,valid_error):
    action={'action':'search','searches':[{'query':'x','company':'甲','mode':'hybrid'}]};usage={'input_tokens':1}
    step=tmp_path/'step_00';step.mkdir()
    (step/'events.jsonl').write_text(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':json.dumps(action)}})+'\n'+json.dumps({'type':'turn.completed','usage':usage})+'\n')
    final={'status':'insufficient','citations':[]}
    es=[{'event':'teacher_decision','role':'planner','step':0,'action':action,'usage':usage},
        {'event':'tool_request','step':0,'tool':'search','args':action['searches'][0]},
        {'event':'tool_response','step':0,'tool':'search','response':{'error':'TimeoutError','detail':'injected','recoverable':True}},
        {'event':'final','response':final}]
    save(tmp_path,es)
    (tmp_path/'result.json').write_text(json.dumps({'final':final,'error':None,'decision_calls':1,'tool_calls':1,'search_calls':1}))
    if valid_error:assert audit_agent_case(tmp_path)['status']=='AUDIT_PASSED'
    else:
        es[2]['response']={};save(tmp_path,es)
        with pytest.raises(ValueError,match='neither results nor error'):audit_agent_case(tmp_path)
