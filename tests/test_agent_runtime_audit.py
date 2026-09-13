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
