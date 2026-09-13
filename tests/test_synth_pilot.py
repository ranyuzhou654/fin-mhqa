import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location('pilot_validator', Path(__file__).parents[1]/'scripts/validate_synth_pilot.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

def row(op='difference', values=('200000000', '50000000'), unit='亿元', digits=2):
    return {'operation':op, 'operand_values_yuan':list(values), 'answer_type':'number',
            'answer_unit':unit, 'round_digits':digits, 'hops':[{'company':'甲'}, {'company':'乙'}]}

def test_exact_decimal_and_conversion():
    assert m.calculate(row()) == m.Decimal('1.50')
    assert m.calculate(row('sum')) == m.Decimal('2.50')
    assert m.calculate(row('spread', ('50000000','200000000'))) == m.Decimal('1.50')

def test_ratio_rounding():
    assert m.calculate(row('ratio_percent', ('1','3'), '%')) == m.Decimal('33.33')

def test_count_strict_threshold():
    r=row('count_gt', ('100','101'), '家', 0);r['threshold_yuan']='100'
    assert m.calculate(r) == 1

def test_ambiguous_entity_rejected():
    r=row('argmax', ('100','100'));r.update(answer_type='entity',answer_unit=None)
    with pytest.raises(ValueError, match='tie'): m.calculate(r)

def test_wrong_unit_and_nonfinite_rejected():
    with pytest.raises(ValueError, match='unit'): m.calculate(row('ratio_percent'))
    with pytest.raises(ValueError, match='nonfinite'): m.number('NaN')

def test_wrong_arity_rejected():
    with pytest.raises(ValueError, match='two operands'): m.calculate(row(values=('1','2','3')))

def evidence_case():
    r=row(values=('2000','3'),unit='元');r.update(id='x',split='dev_pilot_only',generator_model='gpt-5.6-sol',type='comparison',question='甲比乙多多少元？',reasoning_summary='2000-3',answer_value='1997.00')
    chunks={};hops=[]
    for company,value,unit,yuan in [('甲','2','千元','2000'),('乙','3','元','3')]:
        quote=f'2025H1 营业收入 {value} {unit}'
        h=dict(company=company,company_code=company,chunk_id=company,source_sha256=company,page=1,quote=quote,metric='营业收入',period='2025H1',value=value,unit=unit,value_yuan=yuan)
        hops.append(h);chunks[company]=dict(company=company,company_code=company,source_sha256=company,page_start=1,page_end=1,report_period='2025H1',text=quote)
    r['hops']=hops
    return r,chunks

def test_evidence_binding_and_source_callback():
    r,chunks=evidence_case();seen=[]
    assert m.validate_row(r,chunks,lambda h:seen.append(h['chunk_id'])) == '1997.00'
    assert seen==['甲','乙']

@pytest.mark.parametrize('field,value,match', [('quote','虚构金额','verbatim'),('source_sha256','伪造','mismatch'),('value_yuan','2','conversion'),('period','2024H1','period')])
def test_tampered_evidence_rejected(field,value,match):
    r,chunks=evidence_case();r['hops'][0][field]=value
    with pytest.raises(ValueError,match=match): m.validate_row(r,chunks,lambda h:None)

def test_source_failure_is_not_silenced():
    r,chunks=evidence_case()
    def fail(h): raise ValueError('PDF hash mismatch')
    with pytest.raises(ValueError,match='PDF hash'):m.validate_row(r,chunks,fail)
