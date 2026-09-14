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


def test_header_mask_removes_unit_period_scope_without_mutating_source():
    from run_agent_recovery import transform
    original={'results':[{'chunk_id':'a','source_sha256':'hash','company':'甲',
        'text':'2025年上半年合并营业收入 100 千元；2024年母公司收入 80 千元',
        'unit':'千元','report_period':'2025H1','section_path':'合并利润表',
        'table_title':'2025年合并利润表','unit_source':'人民币千元'}]}
    result=transform(original,'header_mask_first')
    shown=str(result['results'][0])
    assert all(term not in shown for term in ('2025','2024','千元','合并','母公司'))
    assert '100' in shown and '80' in shown
    assert result['results'][0]['source_sha256']=='hash'
    assert original['results'][0]['unit']=='千元'
