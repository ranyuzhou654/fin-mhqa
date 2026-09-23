"""Calculator v2 (ratio/growth) and its version gating in the agent schema/prompt."""
from decimal import Decimal
import pytest
from jsonschema import ValidationError, validate
from finmhqa.agent.pilot_tools import calculate
from finmhqa.agent.codex_teacher import SCHEMA
from finmhqa.agent.graph_contracts import PLAN_SCHEMA, CONTEXT_PLAN_SCHEMA, calculator_v2
from finmhqa.agent.graph import AgentGraph


def calc(op, values, unit='%', digits=2):
    return calculate({'operation': op, 'values': [{'value': v, 'unit': u} for v, u in values],
                      'output_unit': unit, 'round_digits': digits})


def test_ratio_normalises_units_before_dividing():
    # 营业成本 1,234,567 千元 / 营业收入 25.1 亿元
    r = calc('ratio', [('1,234,567', '千元'), ('25.1', '亿元')])
    expected = (Decimal(1234567000) / Decimal(2510000000) * 100).quantize(Decimal('0.01'))
    assert r == {'value': str(expected), 'unit': '%', 'operation': 'ratio'} and r['value'] == '49.19'


def test_growth_uses_current_then_prior_and_rounds_half_up():
    assert calc('growth', [('110', '元'), ('100', '元')])['value'] == '10.00'
    assert calc('growth', [('90', '元'), ('100', '元')])['value'] == '-10.00'
    assert calc('growth', [('100.125', '元'), ('100', '元')], digits=3)['value'] == '0.125'
    assert calc('ratio', [('1', '元'), ('8', '元')], digits=1)['value'] == '12.5'
    assert calc('ratio', [('1', '元'), ('16', '元')], digits=1)['value'] == '6.3'   # 6.25 -> half up


@pytest.mark.parametrize('op,values,unit,message', [
    ('ratio', [('1', '元'), ('0', '元')], '%', 'zero'),
    ('growth', [('1', '元'), ('0', '元')], '%', 'positive'),
    ('growth', [('1', '元'), ('-5', '元')], '%', 'positive'),
    ('ratio', [('1', '元')], '%', 'arity'),
    ('ratio', [('1', '元'), ('2', '元'), ('3', '元')], '%', 'arity'),
    ('ratio', [('1', '元'), ('2', '元')], '亿元', 'output_unit'),
    ('convert', [('1', '元')], '%', 'output_unit'),
    ('sum', [('1', '元'), ('2', '元')], '%', 'output_unit'),
])
def test_invalid_ratio_requests_rejected(op, values, unit, message):
    with pytest.raises(ValueError, match=message):
        calc(op, values, unit)


def test_amount_operations_unchanged():
    assert calc('convert', [('26,013,690', '千元')], '亿元') == {'value': '260.14', 'unit': '亿元', 'operation': 'convert'}
    assert calc('difference', [('3', '亿元'), ('1', '亿元')], '万元', 0)['value'] == '20000'


def ratio_action(base):
    action = {'action': 'calculate', 'reason': 'r', 'searches': None, 'final': None,
              'calculation': {'operation': 'ratio', 'values': [{'value': '1', 'unit': '元'}, {'value': '2', 'unit': '元'}],
                              'output_unit': '%', 'round_digits': 2}}
    if 'plan' in base['properties']:
        action['plan'] = [{'id': 'q1', 'question': 'q', 'depends_on': [], 'status': 'pending', 'conclusion': '', 'evidence_ids': []}]
    if 'context' in base['properties']:
        action['context'] = None
    return action


@pytest.mark.parametrize('base', [SCHEMA, PLAN_SCHEMA, CONTEXT_PLAN_SCHEMA])
def test_v1_schema_rejects_ratio_and_v2_accepts_it(base):
    with pytest.raises(ValidationError):
        validate(ratio_action(base), base)
    validate(ratio_action(base), calculator_v2(base))
    frozen = base['properties']['calculation']['anyOf'][1]['properties']   # original frozen object not mutated
    assert frozen['operation']['enum'] == ['convert', 'sum', 'difference'] and '%' not in frozen['output_unit']['enum']


def test_v1_prompt_is_unchanged_and_v2_prompt_documents_ratio():
    seen = {}
    for version in ('v1', 'v2'):
        prompts = []
        def model(q, payload, step, prompt, schema):
            prompts.append((prompt, schema))
            return {'action': 'finish', 'reason': 'r', 'searches': None, 'calculation': None, 'plan': [
                {'id': 'q1', 'question': 'q', 'depends_on': [], 'status': 'pending', 'conclusion': '', 'evidence_ids': []}],
                'final': {'status': 'insufficient', 'answer_value': '', 'answer_unit': '', 'selected_company': None,
                          'citations': [], 'explanation': '不足'}}, {}
        AgentGraph(model, lambda _: {'results': []}, lambda e: None, synthesis='verified', calculator=version).run('q')
        seen[version] = prompts[0]
    assert '计算器v2' not in seen['v1'][0] and seen['v1'][1] == PLAN_SCHEMA
    assert '计算器v2' in seen['v2'][0] and 'growth' in str(seen['v2'][1]['properties']['calculation'])
    with pytest.raises(ValueError, match='calculator'):
        AgentGraph(lambda *a: None, lambda *a: None, lambda e: None, calculator='v3')
