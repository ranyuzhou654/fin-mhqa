import pytest
from finmhqa.ingest.docling_adapter import table_view


def cell(text,r,c,*,rs=1,cs=1,header=False):
    return dict(text=text,start_row_offset_idx=r,end_row_offset_idx=r+rs,
                start_col_offset_idx=c,end_col_offset_idx=c+cs,column_header=header)


def test_span_headers_and_unknown_cells_are_preserved():
    table={'data':{'num_rows':3,'num_cols':3,'table_cells':[
        cell('项目',0,0,rs=2,header=True),cell('本期',0,1,cs=2,header=True),
        cell('金额',1,1,header=True),cell('比例',1,2,header=True),
        cell('收入',2,0),cell('(1,234)',2,1)]}}
    view=table_view(table)
    assert view['headers']==['项目','本期 / 金额','本期 / 比例']
    assert view['rows']==[['收入','(1,234)','']]
    assert view['cells']==table['data']['table_cells']


def test_date_and_restatement_qualifiers_are_not_lost():
    table={'data':{'num_rows':5,'num_cols':2,'table_cells':[
        cell('2024年',0,1,header=True),cell('6月30日止',1,1),
        cell('(经重述)',2,1),cell('净利润',3,0),cell('123',3,1),
        cell('下一个项目',4,0),cell('',4,1)]}}
    view=table_view(table)
    assert view['header_rows']==3
    assert view['headers'][1]=='2024年 / 6月30日止 / (经重述)'
    assert len(view['rows'])==2


def test_invalid_span_rejected():
    with pytest.raises(ValueError,match='outside'):
        table_view({'data':{'num_rows':1,'num_cols':1,'table_cells':[cell('x',0,0,cs=2)]}})
    with pytest.raises(ValueError,match='Overlapping'):
        table_view({'data':{'num_rows':1,'num_cols':1,'table_cells':[cell('x',0,0),cell('y',0,0)]}})


def test_native_corpus_provenance_coverage_and_rejected_table(tmp_path):
    import hashlib
    import json
    import pymupdf
    from finmhqa.ingest.docling_adapter import pages_from_native
    source=tmp_path/'report.pdf'
    with pymupdf.open() as pdf:
        p=pdf.new_page(width=300,height=400)
        p.insert_text((20,40),'Revenue 123')
        pdf.save(source)
    digest=hashlib.sha256(source.read_bytes()).hexdigest()
    native=tmp_path/'0001-0001.json'
    payload=dict(source_sha256=digest,settings_sha256='settings',document=dict(
        pages={'1':{'size':{'width':300,'height':400}}},tables=[dict(self_ref='#/tables/0',
        prov=[dict(page_no=1,bbox=dict(l=10,t=380,r=250,b=330,coord_origin='BOTTOMLEFT'))],
        data=dict(num_rows=2,num_cols=2,table_cells=[cell('Item',0,0,header=True),cell('Value',0,1,header=True),cell('Revenue',1,0),cell('123',1,1)]))]))
    native.write_text(json.dumps(payload))
    pages=pages_from_native(source,'Example',[native],expected_sha256=digest)
    assert pages[0].tables[0].bbox==(10,20,250,70)
    assert pages[0].tables[0].source_refs==[f'{native}#/tables/0']
    assert pages[0].lines==[]  # source text is represented in table, not duplicated
    from finmhqa.ingest.structure import walk_sections
    assert [event[2][0] for event in walk_sections(pages)]==['table']
    with pytest.raises(ValueError,match='coverage'):
        pages_from_native(source,'Example',[],expected_sha256=digest)
    with pytest.raises(ValueError,match='Duplicate'):
        pages_from_native(source,'Example',[native,native],expected_sha256=digest)
    with pytest.raises(ValueError,match='Source hash'):
        pages_from_native(source,'Example',[native],expected_sha256='wrong')
    payload['document']['tables'][0]['data']['table_cells'].append(cell('999',1,1))
    native.write_text(json.dumps(payload))
    pages=pages_from_native(source,'Example',[native],expected_sha256=digest)
    assert pages[0].tables==[]
    assert pages[0].lines[0][1]=='Revenue 123'
    assert 'Overlapping' in pages[0].extraction_issues[0]['error']


def test_normalized_headers_do_not_reinterpret_data():
    from finmhqa.ingest.chunk import split_table
    rows=[['Item','本期','上年同期'],['','本期收入','上年收入'],['Profit','123','456']]
    assert list(split_table(rows,normalized_headers=True))==[rows]


def test_midtable_period_headers_do_not_consume_preceding_numbers():
    from finmhqa.ingest.docling_adapter import table_views
    table={'data':{'num_rows':4,'num_cols':2,'table_cells':[
        cell('项目',0,0,header=True),cell('2025年',0,1,header=True),
        cell('收入',1,0),cell('123',1,1),
        cell('项目',2,0,header=True),cell('2024年',2,1,header=True),
        cell('收入',3,0),cell('99',3,1)]}}
    views=table_views(table)
    assert [v['headers'] for v in views]==[['项目','2025年'],['项目','2024年']]
    assert [v['rows'] for v in views]==[[['收入','123']],[['收入','99']]]
    with pytest.raises(ValueError,match='Multiple'):
        table_view(table)
