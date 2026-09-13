import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
from run_agent_recovery import transform


def sample():return {'results':[{'chunk_id':'c','company':'美的集团','report_period':'2025H1','unit':'千元','text':'2025年上半年，营业收入100千元'}]}

def test_transform_never_mutates_real_response():
    original=sample();changed=transform(original,'unit_mask_first')
    assert '千元' in original['results'][0]['text']
    assert '千元' not in changed['results'][0]['text']
    assert changed['results'][0]['unit']==''

def test_empty_and_period_faults_are_explicit():
    assert transform(sample(),'empty_first')['results']==[]
    r=transform(sample(),'period_mask_first')['results'][0]
    assert '2025' not in r['text'] and r['report_period']==''

def test_synthetic_conflict_retains_original():
    r=transform(sample(),'conflict_first')
    assert r['results'][0]==sample()['results'][0]
    assert r['results'][1]['chunk_id']=='fault-conflict-001'
    assert '冲突' in r['results'][1]['text']


def test_mask_includes_metadata_that_leaked_in_live_v1():
    source=sample();source['results'][0].update(unit_source='page96: 人民币千元',table_title='2025年半年度利润表',section_path='2025年半年报')
    unit=transform(source,'unit_mask_first')['results'][0]
    assert '千元' not in unit['unit_source']
    period=transform(source,'period_mask_first')['results'][0]
    assert '2025' not in period['table_title'] and '2025' not in period['section_path']
    assert source['results'][0]['unit_source']=='page96: 人民币千元'
