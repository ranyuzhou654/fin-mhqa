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
