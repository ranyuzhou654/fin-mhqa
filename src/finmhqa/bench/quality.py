"""benchmark_v1 S4 checks: paraphrase preservation rules, ambiguity-judge contract, retrieval reachability."""
import re
from .generate import OUTSIDE, RATIOS
from .ledger import METRICS

PARAPHRASE_PROMPT = (
    '你负责改写财报问答题的措辞，使其更自然、不那么模板化。必须逐字保留：所有公司名、指标名称、日期或期间、'
    '合并口径或母公司口径的说法、较高/较低/最高等选择条件、减去/之和/占比等运算要求、括号内的公式定义、'
    '金额单位和保留小数位数的要求。不得增加、删除或改变任何条件，不得引入新的数字，不得回答问题。'
    '只输出JSON：{"question": "改写后的题面"}。')
PARAPHRASE_SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['question'],
                     'properties': {'question': {'type': 'string'}}}

JUDGE_PROMPT = (
    '你是评测题质量审核员。背景：题目面向10家A股上市公司的2025年半年度报告，报告含合并报表与母公司报表，'
    '利润表和现金流量表列示2025年上半年与2024年上半年，资产负债表列示2025年6月30日与2024年12月31日。'
    '请只判断题面本身是否有歧义：公司、指标、口径、期间、计算方式、选择条件和答案格式是否都能唯一确定。'
    '不需要回答题目，也不要判断报告里有没有该数据——报告缺少所需内容时正确做法是说明无法回答，这不算歧义。'
    '不调用任何工具。只输出符合schema的JSON。')
ASPECTS = ['company', 'metric', 'scope', 'period', 'calculation', 'selection', 'format', 'other']
JUDGE_SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['ambiguous', 'issues', 'reason'], 'properties': {
    'ambiguous': {'type': 'boolean'},
    'issues': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False, 'required': ['aspect', 'detail'],
                                          'properties': {'aspect': {'type': 'string', 'enum': ASPECTS}, 'detail': {'type': 'string'}}}},
    'reason': {'type': 'string'}}}

COMPANIES = ('五粮液', '亿纬锂能', '伊利股份', '宁德时代', '格力电器', '比亚迪', '泸州老窖', '海尔智家', '美的集团', '贵州茅台') + OUTSIDE
LABELS = sorted({m[2] for m in METRICS.values()} | {r[2] for r in RATIOS.values()}, key=len, reverse=True)
PHRASES = ('含少数股东损益', '合并口径', '母公司', '不是合并口径', '较高', '较低', '最高', '减去', '保留正负号', '两者之和', '合计', '所占比例',
           '同比增长率', '增长率', '亿元', '百分比', '两位小数', '第三季度', '全年', '上年同期', '期初')
PERIOD = re.compile(r'20\d\d\s*年\s*(?:上半年|全年|第三季度|\d{1,2}\s*月\s*\d{1,2}\s*日)')


def required_tokens(question):
    """Tokens a paraphrase must keep verbatim, read from the template question itself."""
    text = question
    labels = []
    for label in LABELS:          # longest first so 净利润 inside 归属于母公司股东的净利润 is not double counted
        if label in text:
            labels.append(label)
            text = text.replace(label, '')
    return sorted({c for c in COMPANIES if c in question} | set(labels) | set(PERIOD.findall(question))
                  | {p for p in PHRASES if p in question} | set(re.findall(r'（[^（）]*=[^（）]*）', question)))


def numbers(text):
    return set(re.findall(r'\d+', text))


def paraphrase_problems(original, candidate):
    problems = [f'missing: {t}' for t in required_tokens(original) if t not in candidate]
    extra = numbers(candidate) - numbers(original)
    if extra:
        problems.append(f'new numbers: {sorted(extra)}')
    for company in COMPANIES:
        if company in candidate and company not in original:
            problems.append(f'new company: {company}')
    if len(candidate) > 2 * len(original):
        problems.append('paraphrase more than twice as long')
    return problems


STATEMENT_NAME = {'IS': '利润表', 'CF': '现金流量表', 'BS': '资产负债表'}


def reachability_query(fact):
    scope = '母公司' if fact['scope'] == 'parent' else '合并'
    period = {'2025H1': '2025年半年度', '2024H1': '2024年半年度 上年同期', '2025-06-30': '2025年6月30日 期末',
              '2024-12-31': '2024年12月31日 期初'}[fact['period']]
    return {'query': f"{fact['company']} {scope}{STATEMENT_NAME[fact['statement']]} {fact['metric_label']} {period}",
            'company': fact['company'], 'mode': 'hybrid'}


def reachable(fact, results):
    """Rank (1-based) of a result carrying the fact: the same chunk, or a chunk of the same table with the raw value."""
    for rank, chunk in enumerate(results, 1):
        if chunk['chunk_id'] == fact['chunk_id'] or (fact['raw_value'] in chunk['text'] and fact['metric_label'][:4] in chunk['text']
                                                    and chunk.get('source_sha256') == fact['source_sha256']):
            return rank
    return None
