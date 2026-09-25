"""Blind fault injection v1: plans, misled answers, deterministic transforms and audit replay."""
from decimal import Decimal
import json
import pytest
from finmhqa.bench.faults import FaultInjector, FaultReplay, Transformer, answer_under, build_plan, format_like

HEAD = '【甲公司 | 2025年半年度报告 | 第八节 财务报告 > 二、财务报表】\n'
SUMMARY = '【甲公司 | 2025年半年度报告 | 第二节 公司简介和主要财务指标 > 主要会计数据】\n'


def chunk(cid, text, page, parent=None, kind='table_md', section='第八节 财务报告 > 二、财务报表', **extra):
    return {'chunk_id': cid, 'company': '甲公司', 'source_sha256': 's', 'report_period': '2025H1', 'section_path': section,
            'page_start': page, 'page_end': page, 'chunk_type': kind, 'text': text, 'parent_table_id': parent, 'part': 0,
            'unit': '', **extra}


def corpus():
    md = chunk('md', HEAD + '表：利润表　单位：未标注　币种：未标注\n| 项目 | 附注 | 本期 | 上期 |\n|---|---|---|---|\n'
               '| 营业收入 |  | 1,200.00 | 1,000.00 |\n| 管理费用 |  | 12,000.00 | 1,200.00 |', 30, 'p1')
    kv = chunk('kv', HEAD + '表：利润表　单位：未标注　币种：未标注\n营业收入：本期 1,200.00，上期 1,000.00', 30, 'p1', 'table_kv')
    other = chunk('other', HEAD + '表：附注　单位：元\n| 营业收入 | 1,200.00 |', 50, 'p2')
    home = chunk('home', SUMMARY + '表：主要会计数据　单位：元\n| 营业收入 | 1,200.00 | 1,000.00 |', 6, 'p3',
                 section='第二节 公司简介和主要财务指标 > 主要会计数据')
    return {c['chunk_id']: c for c in (md, kv, other, home)}


def fact(fid, period, value, raw):
    return {'fact_id': fid, 'company': '甲公司', 'scope': 'consolidated', 'statement': 'IS', 'metric': 'revenue',
            'metric_label': '营业收入', 'period': period, 'value': value, 'raw_value': raw, 'unit': '元',
            'value_yuan': value, 'chunk_id': 'md', 'source_sha256': 's',
            'corroboration': [{'chunk_id': 'other'}], 'unit_source_kind': 'statement_header'}


ADMITTED = {'f-cur': fact('f-cur', '2025H1', '1200.00', '1,200.00'), 'f-pri': fact('f-pri', '2024H1', '1000.00', '1,000.00')}
GROWTH = {'id': 'b1-g', 'type': 'T4', 'derivation': 'growth(f-cur,f-pri)', 'facts': ['f-cur', 'f-pri'],
          'answer': {'status': 'answered', 'value': '20.00', 'unit': '%', 'round_digits': 2, 'selected_company': None}}
AMOUNT = {'id': 'b1-a', 'type': 'T1', 'derivation': 'convert(f-cur)', 'facts': ['f-cur'],
          'answer': {'status': 'answered', 'value': '0.00', 'unit': '亿元', 'round_digits': 2, 'selected_company': None}}


def plan(item, fault):
    return {**build_plan(item, fault, corpus(), ADMITTED, []), 'dataset_sha256': 'd'}


def test_format_like_keeps_decimals_separators_and_negative_style():
    assert format_like('(74,495,747)', Decimal('-74495747') * Decimal('1.07'))[0] == '(79,710,449)'
    assert format_like('-2,516.50', Decimal('-2516.50') * 2)[0] == '-5,033.00'
    assert format_like('1,000.00', Decimal('1070'))[0] == '1,070.00'


def test_uncorrupted_derivation_reproduces_gold_and_faults_give_misled_answers():
    assert answer_under(GROWTH, ADMITTED, {}) == {'value': '20.00', 'unit': '%', 'selected_company': None}
    f1 = plan(GROWTH, 'F1')
    assert f1['wrong_unit'] == '千元' and f1['chunks'] == ['kv', 'md']
    assert not f1['effective']   # numerator and denominator scale together: a neutral fault, reported apart
    f2 = plan(GROWTH, 'F2')
    assert f2['misled']['value'] == '-16.67' and f2['effective']
    f3 = plan(GROWTH, 'F3')
    assert f3['wrong_raw'] == '1,070.00' and f3['misled']['value'] == '12.15'   # target = facts[-1], the prior period
    assert f3['synthetic']['chunk_id'] not in corpus() and '| 营业收入 | 1,200.00 | 1,070.00 |' in f3['synthetic']['text']


def test_f1_and_f2_rewrite_the_whole_parsed_table_but_nothing_else():
    by_id = corpus()
    t = Transformer(plan(AMOUNT, 'F1'), by_id)
    changed = t.chunk(by_id['kv'])
    assert '单位：千元' in changed['text'] and changed['unit'] == '千元' and '单位：未标注' in by_id['kv']['text']
    assert t.chunk(by_id['other']) is by_id['other']
    t = Transformer(plan(GROWTH, 'F2'), by_id)
    md = t.chunk(by_id['md'])['text']
    assert '| 营业收入 |  | 1,000.00 | 1,200.00 |' in md and '| 管理费用 |  | 12,000.00 | 1,200.00 |' in md
    assert '本期 1,000.00，上期 1,200.00' in t.chunk(by_id['kv'])['text']


def test_f3_inserts_once_into_the_first_company_search_and_answers_context_reads():
    by_id = corpus()
    p = plan(AMOUNT, 'F3')
    t = Transformer(p, by_id)
    raw = {'results': [dict(by_id['md'], score=0.9, attached=False)], 'dataset_sha256': 'd'}
    out, note = t.search({'query': 'q', 'company': '乙公司', 'mode': 'hybrid'}, raw)
    assert out == raw and 'inserted' not in note
    out, note = t.search({'query': 'q', 'company': '甲公司', 'mode': 'hybrid'}, raw)
    sid = p['synthetic']['chunk_id']
    assert out['results'][0]['chunk_id'] == sid and out['results'][0]['score'] == 0.9 and note['inserted'] == sid
    assert len(t.search({'query': 'q', 'company': '甲公司', 'mode': 'hybrid'}, raw)[0]['results']) == 1
    assert t.context_args({'anchor_id': sid, 'scope': 'table', 'cursor': 0}) is None
    table, _ = t.read({'anchor_id': sid, 'scope': 'table', 'cursor': 0}, None)
    assert [c['chunk_id'] for c in table['results']] == [sid] and table['total_candidates'] == 1
    assert t.context_args({'anchor_id': sid, 'scope': 'page', 'cursor': 0})['anchor_id'] == 'home'
    page_raw = {'results': [by_id['home']], 'anchor_id': 'home', 'total_candidates': 1, 'text_chars': 10, 'next_cursor': None}
    page, _ = t.read({'anchor_id': sid, 'scope': 'page', 'cursor': 0}, page_raw)
    assert page['anchor_id'] == sid and [c['chunk_id'] for c in page['results']] == ['home', sid] and page['total_candidates'] == 2


class Context:
    def __init__(self, by_id):
        self.by_id = by_id

    def read(self, args):
        c = self.by_id[args['anchor_id']]
        return {'results': [c], 'anchor_id': c['chunk_id'], 'scope': args['scope'], 'total_candidates': 1,
                'text_chars': len(c['text']), 'next_cursor': None}


def test_injector_log_replays_and_detects_tampering(tmp_path):
    by_id = corpus()
    ctx = Context(by_id)
    p = plan(AMOUNT, 'F3')
    log = tmp_path / 'fault_log.jsonl'
    inj = FaultInjector(p, by_id, lambda args: {'results': [dict(by_id['md'], score=0.5)]}, ctx.read, log)
    search_args = {'query': 'q', 'company': '甲公司', 'mode': 'hybrid'}
    seen = [('search', search_args, inj.search(search_args))]
    read_args = {'anchor_id': p['synthetic']['chunk_id'], 'scope': 'page', 'cursor': 0}
    seen.append(('read_evidence_context', read_args, inj.read(read_args)))
    replay = FaultReplay(p, ctx, log)
    for tool, args, response in seen:
        replay.check(tool, args, response)
    assert replay.finish()['inserted']
    replay = FaultReplay(p, ctx, log)
    tampered = json.loads(json.dumps(seen[0][2]))
    tampered['results'][0]['text'] += ' '
    with pytest.raises(ValueError, match='replay mismatch'):
        replay.check('search', search_args, tampered)
    with pytest.raises(ValueError, match='unobserved'):
        FaultReplay(p, ctx, log).finish()
