"""Fact ledger extraction and admission rules on synthetic statement chunks."""
from decimal import Decimal
from finmhqa.bench.ledger import (normalise, parse_number, period_tag, statement_segments, build_company,
                                  check_identities, check_corroboration, admit)

SEC = '第八节 财务报告 > 二、财务报表'


def table(cid, page, order, title, header, rows, unit='元'):
    lines = [f'【甲 | 2025年半年度报告 | {SEC}】', f"表：{title}　单位：{unit or '未标注'}　币种：人民币"]
    for r in ([header] if header else []) + rows:
        lines.append('| ' + ' | '.join(r) + ' |')
        if r is (header or rows[0]):
            lines.append('|' + '---|' * len(r))
    return dict(chunk_id=cid, chunk_type='table_md', company='甲', company_code='000001', source_sha256='s',
                section_path=SEC, page_start=page, page_end=page, table_title=title, unit=unit, _order=order,
                parent_table_id=cid, text='\n'.join(lines))


def text(cid, page, order, body, section=SEC):
    return dict(chunk_id=cid, chunk_type='text', company='甲', company_code='000001', source_sha256='s',
                section_path=section, page_start=page, page_end=page, _order=order, text=f'【甲】\n{body}')


def kv(cid, lines, unit='元', section='第二节 公司简介和主要财务指标 > 主要会计数据'):
    return dict(chunk_id=cid, chunk_type='table_kv', company='甲', company_code='000001', source_sha256='s',
                section_path=section, page_start=5, page_end=5, _order=0, unit=unit, parent_table_id='kv-' + cid,
                text='【甲】\n表：主要会计数据\n' + '\n'.join(lines))


def test_normalise_and_numbers():
    assert normalise("五、净利润（净亏损以'-'号填列）") == '净利润'
    assert normalise('（二）按所有权归属分类 1.归属于母公司股东的净利润') != '归属于母公司股东的净利润'  # merged cell kept unmatched
    assert normalise('其中：营业收入') == '营业收入' and normalise('减：所得税费用') == '所得税费用'
    assert parse_number('(4,695,935)') == (Decimal(-4695935), '(4,695,935)')
    assert parse_number('-') is None and parse_number('四 (60)') is None
    assert period_tag('上年期末数') == 'prior' and period_tag('本期期末数') == 'current' and period_tag('期初余额') == 'prior'


def test_text_header_starts_statement_and_equity_rows_end_it():
    chunks = [
        text('h', 10, 0, '合并利润表\n2025 年1—6 月 单位：元'),
        table('t1', 10, 1, '二、财务报表', ['项目', '附注', '2025年半年度', '2024年半年度'], [['营业收入', '41', '100.00', '90.00']]),
        table('t2', 11, 2, '二、财务报表', None, [['净利润', '', '10.00', '9.00']], unit=None),
        table('eq', 12, 3, '二、财务报表', None, [['一、上年年末余额', '', '1', '2']], unit=None),
    ]
    segments = statement_segments(chunks)
    assert [(s['kind'], s['scope'], [c['chunk_id'] for c in s['chunks']]) for s in segments] == [('IS', 'consolidated', ['t1', 't2'])]


def ledger(chunks):
    records, notes, corr, segments, unit_decl = build_company('甲', chunks)
    check_identities(records)
    check_corroboration(records, {'甲': corr})
    return {(r['scope'], r['metric'], r['period']): r for r in records}, notes


def statements(extra_is=(), tax='(10.00)', cost='(50.00)'):
    header = ['项目', '附注', '2025年半年度', '2024年半年度']
    return [
        table('is', 20, 1, '合并利润表', header, [['营业收入', '43', '100.00', '80.00'], ['营业成本', '', cost, '(40.00)'],
                                                  ['利润总额', '', '40.00', '30.00'], ['减：所得税费用', '', tax, '(5.00)'],
                                                  ['净利润', '', '30.00', '25.00'], ['归属于母公司股东的净利润', '', '28.00', '24.00'],
                                                  ['少数股东损益', '', '2.00', '1.00'], *extra_is]),
        table('bs', 21, 2, '合并资产负债表', ['项目', '期末余额', '期初余额'],
              [['货币资金', '7.00', '6.00'], ['资产总计', '1,000.00', '900.00'], ['负债合计', '600.00', '500.00'],
               ['归属于母公司所有者权益合计', '380.00', '390.00'], ['少数股东权益', '20.00', '10.00'],
               ['所有者权益合计', '400.00', '400.00'], ['负债和所有者权益总计', '1,000.00', '900.00']]),
    ]


def test_identities_confirm_and_note_column_is_skipped():
    facts, _ = ledger(statements())
    revenue = facts[('consolidated', 'revenue', '2025H1')]
    assert revenue['value'] == '100.00' and revenue['period_source'] == 'header'   # note '43' not read as value
    assert facts[('consolidated', 'net_profit', '2025H1')]['identity']['passed']  # 30 = 40 + (-10) in parentheses convention
    assert facts[('consolidated', 'total_assets', '2024-12-31')]['identity']['passed']
    assert facts[('consolidated', 'equity_parent', '2025-06-30')]['identity']['passed']
    assert admit(facts[('consolidated', 'total_assets', '2025-06-30')]) == []
    # revenue has no identity and no independent table -> rejected, never guessed
    assert 'neither identity nor independent table confirms the value' in admit(revenue)


def test_identity_failure_blocks_admission():
    facts, _ = ledger(statements(tax='(12.00)'))
    record = facts[('consolidated', 'net_profit', '2025H1')]
    assert record['identity']['passed'] is False and 'accounting identity failed' in admit(record)


def test_expense_in_parentheses_recorded_as_magnitude_and_corroborated():
    chunks = statements() + [kv('k1', ['营业成本：本报告期 50.00，上年同期 40.00'], unit=None)]
    facts, _ = ledger(chunks)
    cost = facts[('consolidated', 'cost_of_revenue', '2025H1')]
    assert cost['value'] == '50.00' and cost['sign_note']
    assert cost['corroboration'][0]['match'] == 'digits' and admit(cost) == []


def test_corroboration_checks_period_and_unit_conversion():
    chunks = statements() + [kv('k1', ['营业收入：本报告期 0.0100，上年同期 0.0080'], unit='万元')]
    facts, _ = ledger(chunks)
    assert admit(facts[('consolidated', 'revenue', '2025H1')]) == []
    swapped = statements() + [kv('k2', ['营业收入：上年同期 100.00'], unit='元')]
    facts, _ = ledger(swapped)
    assert 'independent table shows this value under the other period' in admit(facts[('consolidated', 'revenue', '2025H1')])


def test_short_digit_strings_do_not_corroborate_without_unit():
    facts, _ = ledger(statements() + [kv('k1', ['营业收入：本报告期 100'], unit=None)])
    assert facts[('consolidated', 'revenue', '2025H1')]['corroboration'] == []


def test_conflicting_values_are_rejected():
    facts, _ = ledger(statements(extra_is=[['营业收入', '', '101.00', '80.00']]))
    assert any('conflicting values' in p for p in facts[('consolidated', 'revenue', '2025H1')]['problems'])


def test_thousand_yuan_rounding_tolerance():
    header = ['项目', '2025年半年度', '2024年半年度']
    cf = table('cf', 30, 1, '合并现金流量表', header, [['经营活动现金流入小计', '223,065,131', '1'],
                                                   ['经营活动现金流出小计', '178,356,177', '1'],
                                                   ['经营活动产生的现金流量净额', '44,708,955', '0']], unit='千元')
    facts, _ = ledger([cf])
    assert facts[('consolidated', 'cfo_net', '2025H1')]['identity']['passed']   # off by one printed digit
