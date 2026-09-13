import copy
import pytest
from finmhqa.qa.equivalence import apply_review,chunk_digest


@pytest.fixture
def reviewed():
    chunks=[dict(chunk_id=i,company_code='A',source_sha256='pdf',page_start=page,unit='元',text=text)
            for i,page,text in [('old',1,'original'),('new',2,'equivalent'),('wrong',3,'unrelated')]]
    q=dict(query_id='q',query='收入是多少',company_code='A',source_sha256='pdf',expected_unit='元',gold_chunk_ids=['old'])
    decisions=[dict(chunk_id=c['chunk_id'],decision=decision,reason='reviewed reason',chunk_sha256=chunk_digest(c),
                    source_page=c['page_start'],source_sha256='pdf',source_terms=['source assertion'])
               for c,decision in zip(chunks,['retain_v1','accept','reject'])]
    discovery=dict(queries=[dict(query_id='q',candidate_ids=['old','new','wrong'],source_pages=[1,2,3])])
    review=dict(status='SOURCE_REVIEW_COMPLETE',label_version='source_equivalence_v2',queries=[dict(query_id='q',source_pages=[1,2,3],decisions=decisions)])
    return [q],chunks,discovery,review


def test_expands_gold_without_changing_question_or_previous(reviewed):
    previous,chunks,discovery,review=reviewed;before=copy.deepcopy(previous);calls=[]
    result=apply_review(*reviewed,lambda q,c,d:calls.append(c['chunk_id']) or True)
    assert previous==before and result[0]['query']==previous[0]['query']
    assert result[0]['gold_chunk_ids']==['new','old'] and calls==['new']


@pytest.mark.parametrize('change',['different_source','missing_unit','changed_text','missing_decision','remove_old','unproved'])
def test_rejects_unverified_or_changed_evidence(reviewed,change):
    previous,chunks,discovery,review=reviewed
    if change=='different_source':chunks[1]['source_sha256']='other'
    elif change=='missing_unit':chunks[1]['unit']='';review['queries'][0]['decisions'][1]['chunk_sha256']=chunk_digest(chunks[1])
    elif change=='changed_text':chunks[1]['text']='changed'
    elif change=='missing_decision':review['queries'][0]['decisions'].pop()
    elif change=='remove_old':review['queries'][0]['decisions'][0]['decision']='reject'
    with pytest.raises(ValueError):apply_review(*reviewed,lambda *a:change!='unproved')
