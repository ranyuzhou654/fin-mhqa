from copy import deepcopy
import pytest
from finmhqa.qa.review import score_review


def fixture():
    chunks=[];samples=[]
    for company in map(str,range(10)):
        for kind in ('text','table_md','table_kv'):
            for i in range(200):
                c=dict(chunk_id=f'{company}-{kind}-{i}',company=company,chunk_type=kind,text=f'Original {i}')
                chunks.append(c)
                if i<10:samples.append(dict(c,breadcrumb_correct=True,table_correct=True,review_notes='source checked'))
    return chunks,samples,[s['chunk_id'] for s in samples]


def test_quality_threshold_and_missing_review():
    chunks,samples,ids=fixture()
    assert score_review(chunks,samples,ids)['status']=='PASSED'
    samples[0]['breadcrumb_correct']=None
    assert score_review(chunks,samples,ids)['status']=='PENDING_REVIEW'
    samples[0]['breadcrumb_correct']=False
    assert score_review(chunks,samples,ids)['status']=='PASSED'  # 29/30 >= .95
    samples[1]['breadcrumb_correct']=False
    assert score_review(chunks,samples,ids)['status']=='FAILED'


def test_changed_or_cherry_picked_evidence_fails():
    chunks,samples,ids=fixture()
    changed=deepcopy(samples);changed[0]['text']='Corrected after seeing answer'
    with pytest.raises(ValueError,match='evidence changed'):
        score_review(chunks,changed,ids)
    with pytest.raises(ValueError,match='fixed audit'):
        score_review(chunks,samples[1:],ids)
    changed=deepcopy(samples);changed[0]['breadcrumb_correct']='true'
    with pytest.raises(ValueError,match='boolean'):
        score_review(chunks,changed,ids)
