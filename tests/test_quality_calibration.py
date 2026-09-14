from copy import deepcopy
from pathlib import Path
import json
import sys
import pytest
from finmhqa.qa.calibration import blank_review,compare,digest,validate_reviews,DIMENSIONS
sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
from score_quality_calibration import load_packet
from build_quality_calibration import build


def case(name='q',status='answered'):
    return {'id':name,'question':'本期合并收入','final':{'status':status},
            'observations':[{'tool':'search','response':{'results':[{'chunk_id':'c','text':'收入100元'}]}}]}


def review(c):
    r=blank_review(c);r.update(reviewer='test fixture',reviewed_at='test only')
    for d in DIMENSIONS:
        r['labels'][d]='supported' if (c['final']['status']=='answered')==(d!='abstention') else 'not_applicable'
        r['rationale'][d]='fixture rationale'
    return r


def test_blank_reference_never_reports_quality():
    c=case();r=compare([c],[blank_review(c)])
    assert r['status']=='PENDING_HUMAN_REFERENCE' and r['metrics'] is None


def test_case_edits_invalidate_signed_labels():
    c=case();r=review(c);c['question']='改动'
    with pytest.raises(ValueError,match='changed case'):compare([c],[r])


def test_assistant_proposal_cannot_be_used_as_human_reference():
    c=case();r=review(c);r['reviewer_type']='assistant'
    with pytest.raises(ValueError,match='human reference'):compare([c],[r])


def test_missing_cases_and_unobserved_rationale_citations_rejected():
    c=case();r=review(c)
    with pytest.raises(ValueError,match='denominator'):compare([c],[])
    r['evidence_ids']['unit_applicability']=['invented']
    with pytest.raises(ValueError,match='unobserved'):compare([c],[r])


def test_abstention_does_not_count_as_supported_answer():
    c=case(status='insufficient');r=review(c);r['labels']['precision']='supported'
    with pytest.raises(ValueError,match='abstention'):compare([c],[r])


def test_false_acceptance_excludes_uncertain_references():
    cs=[case('bad'),case('uncertain')];refs=[review(c) for c in cs];preds=deepcopy(refs)
    refs[0]['labels']['unit_applicability']='missing';refs[1]['labels']['unit_applicability']='uncertain'
    r=compare(cs,refs,preds);m=r['metrics']['unit_applicability']
    assert m['eligible']==1 and m['false_accept_rate']==1 and m['reference_uncertain']==1
    assert r['release_approved'] is False


def test_actual_packet_freezes_12_records_6_edits_and_no_verifier_leak(tmp_path):
    root=tmp_path/'packet';cases=build(root);loaded,manifest=load_packet(root)
    assert loaded==cases and len(cases)==18 and manifest['actual_records']==12
    assert len({p['group'] for p in manifest['cases']})==12
    for c in cases:
        assert set(c)=={'id','question','final','observations'}
        assert all(set(o)=={'step','tool','args','response'} for o in c['observations'])
        payload=json.loads((root/'judge_inputs'/(c['id']+'.json')).read_text())
        assert set(payload)=={'rubric','case'}
    assert compare(cases,json.loads((root/'reference.template.json').read_text()))['metrics'] is None
    (root/'judge_inputs'/(cases[0]['id']+'.json')).write_text('{}')
    with pytest.raises(ValueError,match='packet changed'):load_packet(root)
