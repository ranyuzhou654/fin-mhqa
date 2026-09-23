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


def test_report_level_unit_declaration_ranked_before_local_units(tmp_path):
    a=dict(chunk('a',65),section_path='第八节财务报告 > 二、财务报表')
    local=[dict(chunk(f'l{i}',20+i),section_path='第三节管理层讨论与分析',text=f'【甲】\n表格说明 单位：万元 第{i}段') for i in range(7)]
    decl=dict(chunk('decl',63),section_path='第八节财务报告 > 二、财务报表',text='【甲】\n财务报表及附注的单位为：千元')
    chunks=[a,*local,decl]
    assert reader(tmp_path,chunks).read(dict(anchor_id='a',scope='document_units',cursor=0))['results'][0]==decl
    (tmp_path/'v1').mkdir()
    v1=reader(tmp_path/'v1',chunks); v1=EvidenceContext(tmp_path/'v1',v1.dataset,version=1)
    first=v1.read(dict(anchor_id='a',scope='document_units',cursor=0))
    assert decl not in first['results'] and first['next_cursor']==6


def test_table_continuation_offers_only_unlabeled_next_page_parts_in_same_section(tmp_path):
    sec='财务报表'
    def table(cid,page,parent,title):
        return dict(chunk(cid,page),chunk_type='table_md',parent_table_id=parent,section_path=sec,text=f'【甲】\n表：{title}\n| 行 | 值 |')
    head=table('head',31,'t','合并现金流量表　单位：元')
    cont=table('cont',32,'u','二、财务报表　单位：未标注　币种：未标注')
    labeled=table('labeled',32,'v','母公司现金流量表　单位：元')
    far=table('far',33,'w','二、财务报表　单位：未标注')
    other=dict(table('other',32,'x','二、财务报表　单位：未标注'),section_path='附注')
    r=reader(tmp_path,[head,cont,labeled,far,other])
    t=r.read(dict(anchor_id='head',scope='table',cursor=0))
    assert t['results']==[head] and t['continuation_candidates']==1
    c=r.read(dict(anchor_id='head',scope='table_continuation',cursor=0))
    assert c['results']==[cont] and 'not proven' in c['relation_note']
    v1=EvidenceContext(tmp_path,r.dataset,version=1)
    assert 'continuation_candidates' not in v1.read(dict(anchor_id='head',scope='table',cursor=0))
    with pytest.raises(ValueError,match='scope'):v1.read(dict(anchor_id='head',scope='table_continuation',cursor=0))


def test_table_continuation_requires_table_anchor(tmp_path):
    r=reader(tmp_path,[chunk('plain',1)])
    with pytest.raises(ValueError,match='table anchor'):r.read(dict(anchor_id='plain',scope='table_continuation',cursor=0))
