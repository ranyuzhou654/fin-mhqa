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
