import importlib.util
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
from evaluate_agent_dev import score_case


def test_missing_result_stays_in_denominator(tmp_path):
    result=score_case({'id':'x'}, {}, tmp_path)
    assert result['status']=='NOT_RUN' and result['correct'] is False


def test_failed_run_cannot_score_correct(tmp_path):
    final={'status':'answered','answer_value':'1.00','answer_unit':'亿元','selected_company':None,'citations':[]}
    r={'id':'x','final':final,'error':'transport failed','usage':[]}
    (tmp_path/'result.json').write_text(json.dumps(r))
    (tmp_path/'trajectory.jsonl').write_text(json.dumps({'event':'final','response':final})+'\n')
    row={'id':'x','type':'comparison','answer_value':'1.00','answer_unit':'亿元','hops':[]}
    assert not score_case(row,{},tmp_path)['correct']


def test_wrong_selected_company_not_correct(tmp_path):
    final={'status':'answered','answer_value':'1.00','answer_unit':'亿元','selected_company':'乙','citations':[]}
    (tmp_path/'result.json').write_text(json.dumps({'id':'x','final':final,'error':None,'usage':[]}))
    (tmp_path/'trajectory.jsonl').write_text(json.dumps({'event':'final','response':final})+'\n')
    row={'id':'x','type':'bridge','answer_value':'1.00','answer_unit':'亿元','hops':[]}
    assert not score_case(row,{'selected_company':'甲'},tmp_path)['correct']
