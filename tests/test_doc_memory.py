"""Document-structure memory: write gate, location-only storage, and scoped hints."""
from finmhqa.memory.doc_memory import DocMemory, normalize, row_labels, scope_of

MD = {'chunk_id': 'md1', 'company': '甲', 'source_sha256': 's', 'parent_table_id': 'p1', 'chunk_type': 'table_md', 'page_start': 30,
      'table_title': '3、合并利润表', 'section_path': '第八节 > 3、合并利润表',
      'text': '【甲 | 2025年半年度报告】\n表：3、合并利润表　单位：元\n| 项目 | 2025年半年度 | 2024年半年度 |\n|---|---|---|\n'
              '| 一、营业总收入 | 100 | 90 |\n| 其中：营业收入 | 100 | 90 |\n| 研发费用 | 5 | 4 |'}
PARENT = {**MD, 'chunk_id': 'md2', 'parent_table_id': 'p2', 'table_title': '4、母公司利润表', 'section_path': '第八节 > 4、母公司利润表'}
TEXT = {**MD, 'chunk_id': 't1', 'chunk_type': 'text'}
INDEX = {c['chunk_id']: c for c in (MD, PARENT, TEXT)}
ANSWER = {'status': 'answered', 'citations': ['md1']}


def test_row_labels_and_scope():
    assert normalize('其中：营业收入') == '营业收入' and normalize('一、营业总收入') == '营业总收入'
    assert row_labels(MD) == ['营业总收入', '营业收入', '研发费用']
    assert scope_of(MD) == 'consolidated' and scope_of(PARENT) == 'parent'
    assert scope_of({**MD, 'table_title': '合并及公司利润表'}) == 'both'


def test_only_published_answers_and_indexed_tables_are_learned():
    m = DocMemory()
    assert m.learn({'status': 'insufficient', 'citations': ['md1']}, 'r', INDEX)['written'] == []
    report = m.learn({'status': 'answered', 'citations': ['md1', 'injected', 't1']}, 'r', INDEX)
    assert report['written'] == ['md1']
    assert report['refused'] == {'injected': 'not in the admitted index', 't1': 'not a table chunk'}


def test_labels_come_from_the_index_not_from_the_observed_text():
    m = DocMemory()
    m.learn(ANSWER, 'r', INDEX)                                   # the run may have seen a tampered md1; memory reads INDEX
    labels = {r[0] for r in m.db.execute('SELECT row_label FROM doc_rows')}
    assert labels == {'营业总收入', '营业收入', '研发费用'}
    assert not m.db.execute("SELECT * FROM sqlite_master WHERE sql LIKE '%value%'").fetchall()   # no values stored


def test_hints_match_company_metric_and_scope():
    m = DocMemory()
    m.learn(ANSWER, 'r1', INDEX)
    m.learn({'status': 'answered', 'citations': ['md2']}, 'r2', INDEX)
    assert m.hints('甲2025年上半年合并口径研发费用是多少？', ['甲', '乙']) == ['md1']
    assert m.hints('甲母公司报表中营业收入是多少？', ['甲', '乙']) == ['md2']
    assert m.hints('乙的研发费用是多少？', ['甲', '乙']) == []          # nothing remembered for 乙
    assert m.hints('甲的货币资金是多少？', ['甲', '乙']) == []          # metric never seen


def test_reuse_is_counted():
    m = DocMemory()
    m.learn(ANSWER, 'r1', INDEX)
    m.learn(ANSWER, 'r2', INDEX)
    assert m.db.execute("SELECT uses, first_run, last_run FROM doc_rows WHERE row_label='研发费用'").fetchone() == (2, 'r1', 'r2')
