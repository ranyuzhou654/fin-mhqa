from decimal import Decimal
import importlib.util
from pathlib import Path
import pytest
from finmhqa.agent.pilot_tools import calculate, decimal
from finmhqa.agent.codex_teacher import CodexTeacher
spec=importlib.util.spec_from_file_location('freeze_bridge',Path(__file__).parents[1]/'scripts/freeze_bridge_pilot.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

def fixture():
    return {'type':'bridge','bridge_kind':'conditional_entity_selection','answer_unit':'亿元','round_digits':2,'hops':[
        {'hop_id':'a','company':'甲','company_code':'a','metric':'营业收入','value_yuan':'200','chunk_id':'a'},
        {'hop_id':'b','company':'乙','company_code':'b','metric':'营业收入','value_yuan':'100','chunk_id':'b'},
        {'hop_id':'c','company':'甲','company_code':'a','metric':'归母净利润','value_yuan':'123456789','chunk_id':'c'}],
        'derivation':{'operation':'select_then_read','selector':'argmax','selection_hop_ids':['a','b'],'target_hop_id':'c'}}

def test_select_new_metric():assert m.expected(fixture())==('1.23','甲')

def test_wrong_company_rejected():
    r=fixture();r['hops'][2]['company_code']='b'
    with pytest.raises(ValueError,match='wrong selected'):m.expected(r)

def test_fake_bridge_rejected():
    r=fixture();r['hops'][2]['metric']='营业收入'
    with pytest.raises(ValueError,match='dependency'):m.expected(r)

def test_tie_rejected():
    r=fixture();r['hops'][1]['value_yuan']='200'
    with pytest.raises(ValueError,match='tie'):m.expected(r)

def test_mixed_units_and_rounding():
    assert calculate({'operation':'difference','values':[{'value':'200000','unit':'千元'},{'value':'50000000','unit':'元'}],'output_unit':'亿元','round_digits':2})['value']=='1.50'

def test_bad_calculator_input():
    with pytest.raises(ValueError):decimal('Infinity')
    with pytest.raises(ValueError):calculate({'operation':'__import__','values':[{'value':'1','unit':'元'}],'output_unit':'元','round_digits':0})

def test_teacher_cwd_must_be_empty(tmp_path):
    (tmp_path/'gold.json').write_text('secret')
    with pytest.raises(ValueError,match='empty'):CodexTeacher('/bin/false',tmp_path)

audit_spec=importlib.util.spec_from_file_location('audit_trajectory',Path(__file__).parents[1]/'scripts/audit_teacher_trajectory.py');audit=importlib.util.module_from_spec(audit_spec);audit_spec.loader.exec_module(audit)

def recorded_case(tmp_path):
    import json
    step=tmp_path/'step_00';step.mkdir()
    final={'status':'insufficient','citations':[]}
    action={'action':'finish','final':final};usage={'input_tokens':5,'output_tokens':2}
    events=[{'type':'item.completed','item':{'type':'agent_message','text':json.dumps(action)}},{'type':'turn.completed','usage':usage}]
    (step/'events.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in events))
    rows=[{'event':'teacher_decision','step':0,'action':action,'usage':usage},{'event':'final','response':final}]
    (tmp_path/'trajectory.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in rows))
    (tmp_path/'result.json').write_text(json.dumps({'final':final,'error':None}))
    return rows

def test_actual_event_consistency(tmp_path):
    recorded_case(tmp_path)
    assert audit.audit_case(tmp_path)['status']=='AUDIT_PASSED'

def test_rewritten_decision_rejected(tmp_path):
    import json
    rows=recorded_case(tmp_path);rows[0]['action']['action']='search'
    (tmp_path/'trajectory.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in rows))
    with pytest.raises(ValueError,match='actual teacher'):audit.audit_case(tmp_path)

def test_unapproved_native_tool_rejected(tmp_path):
    import json
    recorded_case(tmp_path)
    p=tmp_path/'step_00/events.jsonl'
    with p.open('a') as f:f.write(json.dumps({'type':'item.completed','item':{'type':'command_execution','command':'cat gold'}})+'\n')
    with pytest.raises(ValueError,match='native tool'):audit.audit_case(tmp_path)
