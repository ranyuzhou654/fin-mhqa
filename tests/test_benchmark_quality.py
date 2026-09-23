"""S4 rules: a paraphrase must keep every condition verbatim and add nothing."""
from finmhqa.bench.quality import required_tokens, paraphrase_problems, reachable

Q = ('根据半年报，在亿纬锂能、比亚迪、美的集团三家公司中，找出2025年上半年合并口径净利润最高的公司，再计算该公司'
     '2025年6月30日合并口径的资产负债率。（资产负债率=负债合计÷资产总计）结果以百分比表示，四舍五入保留两位小数。')


def test_required_tokens_cover_every_condition():
    tokens = required_tokens(Q)
    for t in ('亿纬锂能', '比亚迪', '美的集团', '净利润', '资产负债率', '2025年上半年', '2025年6月30日', '合并口径',
              '最高', '百分比', '两位小数', '（资产负债率=负债合计÷资产总计）'):
        assert t in tokens
    # longest label wins: 归属于母公司股东的净利润 does not also require a bare 净利润 match elsewhere
    assert '净利润' not in required_tokens('贵州茅台2025年上半年合并口径归属于母公司股东的净利润是多少？')


def test_faithful_paraphrase_passes_and_changed_conditions_fail():
    good = ('请根据半年报，从亿纬锂能、比亚迪、美的集团中选出2025年上半年合并口径净利润最高者，并计算其2025年6月30日'
            '合并口径的资产负债率（资产负债率=负债合计÷资产总计），结果以百分比表示、四舍五入保留两位小数。')
    assert paraphrase_problems(Q, good) == []
    assert any('最高' in p for p in paraphrase_problems(Q, good.replace('最高', '最低')))
    assert any('2025年6月30日' in p for p in paraphrase_problems(Q, good.replace('2025年6月30日', '期末')))
    assert any('new numbers' in p for p in paraphrase_problems(Q, good + '参考值约为64。'))
    assert any('new company' in p for p in paraphrase_problems(Q, good.replace('美的集团', '美的集团与格力电器')))


def test_reachability_accepts_same_chunk_or_same_value_in_same_source():
    fact = {'chunk_id': 'c1', 'raw_value': '1,234.56', 'metric_label': '营业收入', 'source_sha256': 's'}
    assert reachable(fact, [{'chunk_id': 'x', 'text': '无关', 'source_sha256': 's'}, {'chunk_id': 'c1', 'text': ''}]) == 2
    assert reachable(fact, [{'chunk_id': 'kv', 'text': '营业收入 1,234.56', 'source_sha256': 's'}]) == 1
    assert reachable(fact, [{'chunk_id': 'kv', 'text': '营业收入 1,234.56', 'source_sha256': 'other'}]) is None
