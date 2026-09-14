import hashlib
import json
from copy import deepcopy
import pytest
from finmhqa.agent.evidence_context import EvidenceContext


def chunk(cid, page, **kw):
    return dict(chunk_id=cid,company='甲',source_sha256='source-a',report_period='2025H1',
                page_start=page,page_end=page,chunk_type='text',text='原文',**kw)


def reader(tmp_path, chunks):
    raw=''.join(json.dumps(c,ensure_ascii=False)+'\n' for c in chunks).encode()
    sha=hashlib.sha256(raw).hexdigest()
    (tmp_path/'chunk_meta.jsonl').write_bytes(raw)
    (tmp_path/'manifest.json').write_text(json.dumps({'count':len(chunks),'admission':{'status':'ACCEPTED','dataset_sha256':sha}}))
    return EvidenceContext(tmp_path,sha)


def test_adjacent_pages_preserve_text_and_do_not_cross_sources(tmp_path):
    a=chunk('a',10); b=chunk('b',9); other=dict(chunk('other',9),source_sha256='source-b')
    r=reader(tmp_path,[a,b,other])
    result=r.read(dict(anchor_id='a',scope='previous_page',cursor=0))
    assert result['results']==[b]
    result['results'][0]['text']='tampered'
    assert r.read(dict(anchor_id='a',scope='previous_page',cursor=0))['results']==[b]


def test_table_parts_are_returned_even_on_next_page(tmp_path):
    a=dict(chunk('a',10),chunk_type='table_kv',parent_table_id='t')
    tables=[dict(chunk(str(i),10+i),chunk_type='table_md',parent_table_id='t',part=i) for i in range(2)]
    unrelated=dict(tables[0],chunk_id='other',parent_table_id='u')
    r=reader(tmp_path,[a,*tables,unrelated])
    assert r.read(dict(anchor_id='a',scope='table',cursor=0))['results']==tables


def test_declarations_are_candidates_not_silent_unit_inheritance(tmp_path):
    a=chunk('a',10); decl=dict(chunk('decl',30),text='本财务报表除特别说明外以人民币千元为单位。')
    r=reader(tmp_path,[a,decl])
    result=r.read(dict(anchor_id='a',scope='document_units',cursor=0))
    assert result['results']==[decl] and 'unit' not in r.by_id['a']


def test_pagination_and_character_bound_are_explicit(tmp_path):
    cs=[dict(chunk(str(i),1),text='原'*5000) for i in range(9)]
    r=reader(tmp_path,cs); a=dict(anchor_id='0',scope='page',cursor=0)
    first=r.read(a); second=r.read(dict(a,cursor=first['next_cursor']))
    assert len(first['results'])==4 and first['text_chars']==20000
    assert not set(c['chunk_id'] for c in first['results']) & set(c['chunk_id'] for c in second['results'])
    assert first['total_candidates']==9
    with pytest.raises(ValueError,match='cursor'):r.read(dict(a,cursor=True))
    with pytest.raises(ValueError,match='cursor'):r.read(dict(a,cursor=10))
    with pytest.raises(ValueError,match='unknown'):r.read(dict(a,anchor_id='unknown'))


def test_changed_dataset_refused(tmp_path):
    r=reader(tmp_path,[chunk('a',1)])
    with (tmp_path/'chunk_meta.jsonl').open('a') as f:f.write(' ')
    with pytest.raises(ValueError,match='dataset'):EvidenceContext(tmp_path,r.dataset)
