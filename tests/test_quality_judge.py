import pytest
from jsonschema import ValidationError
from finmhqa.qa.judge import schema_for,bind
from finmhqa.qa.calibration import DIMENSIONS,digest


def case(status='answered'):
    return {'id':'q','question':'收入','final':{'status':status},'observations':[{'response':{'results':[{'chunk_id':'seen'}]}}]}


def decision(c):
    return {'labels':{d:schema_for(c)['properties']['labels']['properties'][d]['enum'][0] for d in DIMENSIONS},
            'rationale':{d:'fixture explanation' for d in DIMENSIONS},'evidence_ids':{d:[] for d in DIMENSIONS},'notes':''}


def test_runner_binds_model_attribution_and_content():
    c=case();r=bind(c,decision(c),'fixture timestamp')
    assert r['case_sha256']==digest(c) and r['reviewer_type']=='model'


def test_judge_cannot_invent_supporting_evidence_id():
    c=case();d=decision(c);d['evidence_ids']['unit_applicability']=['invented']
    with pytest.raises(ValidationError):bind(c,d,'fixture')


def test_refusal_schema_cannot_turn_into_good_answer():
    c=case('insufficient');d=decision(c);d['labels']['numeric_grounding']='supported'
    with pytest.raises(ValidationError):bind(c,d,'fixture')


def test_empty_rationale_not_accepted():
    c=case();d=decision(c);d['rationale']['precision']=' '
    with pytest.raises(ValueError,match='rationale'):bind(c,d,'fixture')
