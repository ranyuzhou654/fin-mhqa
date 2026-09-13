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
