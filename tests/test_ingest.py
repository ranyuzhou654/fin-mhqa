import pytest
from finmhqa.ingest.clean import clean_text, heading_level, is_toc_page
from finmhqa.ingest.extract import Page, RawTable, parse_table_header
from finmhqa.ingest.structure import merge_cross_page_tables
from finmhqa.ingest.chunk import chunk_pages, split_bounded, split_table, run


def test_cleanup_preserves_numbers():
    assert '123' in clean_text('营业收入\n123\n净利润\n45')
    assert heading_level('1.23 亿元') == 0
    assert heading_level('(一) 经营情况') == 3
    assert is_toc_page('\n'.join(f'章节{i}......{i}' for i in range(8)))
    assert parse_table_header('主要数据表\n单位：百万元 币种：人民币') == ('主要数据表', '百万元', '人民币')


def test_hard_limit_and_stable_provenance():
    text = '长句' * 700
    assert ''.join(split_bounded(text, len)) == text
    pages = [Page('A', 1, '第一节 概况\n'+text), Page('A', 2, '第二节 风险\n风险。')]
    chunks = chunk_pages('A', pages, len, source_sha256='abc')
    assert chunks == chunk_pages('A', pages, len, source_sha256='abc')
    assert all(c['n_tokens'] <= 450 for c in chunks)
    assert next(c for c in chunks if '风险。' in c['text'])['page_start'] == 2


def test_spatial_sections_and_three_page_table():
    pages = []
    for i in range(3):
        rows = [['项目', '金额'], [f'科目{i}', str(i+1)]]
        table = RawTable(i+1, rows, (0, 80 if i == 0 else 5, 100, 95), '明细表', '元')
        pages.append(Page('A', i+1, 'text', [table], [(1, '第一节 财务')] if i == 0 else [], 100))
    # Continuation-only pages have no remaining tables after merge.
    merged = merge_cross_page_tables(pages)
    assert len(merged[0].tables[0].rows) == 4
    assert merged[0].tables[0].page_end == 3
    assert len(pages[0].tables[0].rows) == 2
    chunks = chunk_pages('A', pages, len, source_sha256='abc')
    tables = [c for c in chunks if c['chunk_type'].startswith('table')]
    assert len(tables) == 2
    assert all(c['page_end'] == 3 and c['section_path'] == '第一节 财务' for c in tables)
    assert tables[0]['parent_table_id'] == tables[1]['parent_table_id']


def test_table_before_next_heading():
    p = Page('A', 1, 'text', [RawTable(1, [['项目','金额'],['收入','2']], (0,20,100,30))],
             [(1,'第一节 财务'), (40,'第二节 风险')], 100)
    chunks = chunk_pages('A', [p], len, source_sha256='abc')
    assert all(c['section_path'] == '第一节 财务' for c in chunks)


def test_wide_tables_and_decision_gate(tmp_path):
    parts = list(split_table([['项目'] + [str(i) for i in range(15)], ['收入'] + ['1'] * 15]))
    assert len(parts) == 2 and all(p[1][0] == '收入' for p in parts)
    with pytest.raises(ValueError, match='empty'):
        run(tmp_path, tmp_path/'out', {}, tmp_path)


def test_numbers_and_company_are_not_deduped():
    from finmhqa.ingest.chunk import dedup
    base = dict(company='A', source_sha256='abc', chunk_type='text', section_level=1)
    rows = [dict(base, text='breadcrumb\n营业收入100万元，业务稳定。'),
            dict(base, text='breadcrumb\n营业收入101万元，业务稳定。'),
            dict(base, company='B', text='breadcrumb\n营业收入100万元，业务稳定。')]
    assert len(dedup(rows)) == 3


def test_text_page_range():
    pages = [Page('A', 1, '第一节 概况\n营业收入增长。'), Page('A', 2, '经营情况稳定。')]
    chunk = chunk_pages('A', pages, len, source_sha256='abc')[0]
    assert (chunk['page_start'], chunk['page_end']) == (1, 2)


def test_multiline_heading_and_changed_period():
    page = Page('A', 1, '第三节\n管理层讨论与分析\n一、经营情况\n内容。')
    c = chunk_pages('A', [page], len, source_sha256='abc')[0]
    assert c['section_path'] == '第三节 管理层讨论与分析 > 一、经营情况'
    rows = [['指标','本报告期','上年同期'],['营收','100','90'],['','本报告期末','上年度末'],['总资产','500','400']]
    parts = list(split_table(rows))
    assert len(parts) == 2
    assert parts[1][0][1] == '本报告期末'


def test_two_level_header_retains_every_value():
    rows = [['项目','产品','','地区',''],['','茅台酒','系列酒','国内','国外'],['收入','100','20','110','10']]
    parts = list(split_table(rows))
    assert parts[0][0] == ['项目','产品 / 茅台酒','产品 / 系列酒','地区 / 国内','地区 / 国外']
    assert parts[0][1] == rows[2]


def test_borderless_statement_preserves_period_and_note_columns():
    from finmhqa.ingest.borderless import normalize_statement
    rows = [['','','','截至2025年','截至2024年'],['','','','6月30日止','6月30日止'],
            ['','','附注七','6个月期间','6个月期间'],['一、','营业收入','43','371,280,948','301,126,713'],
            ['','研发费用','47','29,596,366','19,620,756']]
    result = normalize_statement(rows)
    assert result[0] == ['项目','附注七','截至2025年 6月30日止 6个月期间','截至2024年 6月30日止 6个月期间']
    assert result[2] == ['研发费用','47','29,596,366','19,620,756']
    assert normalize_statement([['收入','金额'],['其他','12']]) is None


def test_simhash_repetition_overflow_and_unchanged_normal_hashes():
    from simhash import Simhash
    from finmhqa.ingest.chunk import text_simhash, dedup
    for text in ('', '营业收入100万元，增长。', 'Capital Expenditure 2025', '-----'):
        assert text_simhash(text)==Simhash(text).value
    assert isinstance(text_simhash('重复' * 1000), int)
    base=dict(company='A',source_sha256='abc',chunk_type='table_md',section_level=1,parent_table_id='one')
    first=dict(base,text='breadcrumb\n'+('0 | ' * 1000))
    second=dict(first,text=first['text']+'1')
    assert dedup([first,first,second])==[first,second]
