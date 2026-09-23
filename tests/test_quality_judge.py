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


@pytest.mark.parametrize('tamper',['none','prompt','labels','native_tool'])
def test_native_event_and_input_audit(tmp_path,tamper):
    import json,sys
    from pathlib import Path
    sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
    from audit_quality_judge import audit_case
    from finmhqa.qa.judge import system_prompt
    c=case();d=decision(c);r=bind(c,d,'fixture');call=tmp_path/'call';call.mkdir()
    (call/'input.txt').write_text(system_prompt('rubric')+'\n'+json.dumps({'question':c['question'],'history':c},ensure_ascii=False))
    (call/'schema.json').write_text(json.dumps(schema_for(c)))
    (call/'decision.json').write_text(json.dumps(d));(call/'usage.json').write_text('{}')
    events=[{'type':'thread.started','thread_id':'unique-fixture'},
            {'type':'item.completed','item':{'type':'agent_message','text':json.dumps(d)}},
            {'type':'turn.completed','usage':{}}]
    if tamper=='native_tool':events.insert(1,{'type':'item.completed','item':{'type':'command_execution'}})
    (call/'events.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in events))
    if tamper=='prompt':(call/'input.txt').write_text('leaked reference labels')
    if tamper=='labels':r['labels']['precision']='missing'
    (tmp_path/'result.json').write_text(json.dumps({'id':c['id'],'case_sha256':digest(c),'review':r,'error':None,'usage':{}}))
    if tamper=='none':assert audit_case(c,tmp_path,'rubric')['status']=='AUDIT_PASSED'
    else:
        with pytest.raises(ValueError):audit_case(c,tmp_path,'rubric')


def test_model_only_judge_comparison_counts_edits_and_rule_hits():
    import sys
    from pathlib import Path
    sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
    from compare_judges import compare,unseen_citations
    from finmhqa.qa.calibration import blank_review
    case={'id':'x','question':'q','final':{'status':'answered','citations':['a','ghost']},
          'observations':[{'response':{'results':[{'chunk_id':'a'}]}}]}
    def review(label):
        r=blank_review(case);r['labels']={d:label for d in r['labels']};r['labels']['abstention']='not_applicable';return r
    report=compare([case],{'x':{'variant':'unseen_citation'}},{'j1':[review('supported')],'j2':[review('missing')]})
    assert unseen_citations(case)==['ghost'] and report['deterministic_rule_hits'][0]['id']=='x'
    assert report['controlled_edits']['target_flagged_missing_or_contradicted']=={'j1':0,'j2':1}
    assert report['pairwise_agreement']['j1~j2']['agree']==0 and len(report['disputed_dimensions'])==6
