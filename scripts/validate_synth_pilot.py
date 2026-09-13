"""Validate source bindings and arithmetic; never an independent semantic judge."""
import argparse
from collections import Counter
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path
import re

UNITS = {'元': Decimal(1), '千元': Decimal(1000), '万元': Decimal(10000), '亿元': Decimal(100000000)}

def number(value):
    result = Decimal(str(value).replace(',', ''))
    if not result.is_finite():
        raise ValueError('nonfinite number')
    return result

def calculate(row):
    values = [number(v) for v in row['operand_values_yuan']]
    if len(values) < 2:
        raise ValueError('at least two operands required')
    op = row['operation']
    if op in ('argmax', 'argmin'):
        target = (max if op == 'argmax' else min)(values)
        if values.count(target) != 1:
            raise ValueError('ambiguous entity tie')
        if row['answer_type'] != 'entity' or row['answer_unit'] is not None:
            raise ValueError('entity schema mismatch')
        return row['hops'][values.index(target)]['company']
    if row['answer_type'] != 'number':
        raise ValueError('numeric answer required')
    if op in ('difference', 'ratio_percent') and len(values) != 2:
        raise ValueError('binary operation requires two operands')
    if op == 'difference': result = values[0] - values[1]
    elif op == 'sum': result = sum(values)
    elif op == 'spread': result = max(values) - min(values)
    elif op == 'ratio_percent': result = values[0] / values[1] * 100
    elif op == 'count_gt': result = Decimal(sum(v > number(row['threshold_yuan']) for v in values))
    else: raise ValueError('unsupported operation')
    expected_unit = '%' if op == 'ratio_percent' else '家' if op == 'count_gt' else None
    if expected_unit:
        if row['answer_unit'] != expected_unit: raise ValueError('operation unit mismatch')
    else: result /= UNITS[row['answer_unit']]
    digits = row['round_digits']
    if type(digits) is not int or not 0 <= digits <= 8: raise ValueError('invalid rounding precision')
    return result.quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)

def validate_row(row, chunks, source_check):
    if row['split'] != 'dev_pilot_only' or row['generator_model'] != 'gpt-5.6-sol':
        raise ValueError('provenance/split mismatch')
    if row['type'] not in ('comparison', 'aggregation', 'bridge'): raise ValueError('unknown type')
    if not row['question'].strip() or not row['reasoning_summary'].strip(): raise ValueError('missing question/derivation')
    hops = row['hops']
    if len(hops) != len(row['operand_values_yuan']): raise ValueError('operand/hop mismatch')
    if len({h['source_sha256'] for h in hops}) < 2: raise ValueError('requires two reports')
    if len({h['chunk_id'] for h in hops}) != len(hops): raise ValueError('duplicate evidence')
    for hop, operand in zip(hops, row['operand_values_yuan']):
        chunk = chunks[hop['chunk_id']]
        for field in ('company', 'company_code', 'source_sha256'):
            if hop[field] != chunk[field]: raise ValueError(f'evidence {field} mismatch')
        if not chunk['page_start'] <= hop['page'] <= chunk['page_end']: raise ValueError('page mismatch')
        if not hop['quote'] or hop['quote'] not in chunk['text']: raise ValueError('quote not verbatim')
        normalized = re.sub(r'[\s,，]', '', hop['quote'])
        if re.sub(r'[\s,，]', '', str(hop['value'])) not in normalized: raise ValueError('value absent from quote')
        if hop['metric'] not in re.sub(r'\s', '', chunk['text']): raise ValueError('metric absent from evidence')
        if hop['unit'] not in chunk['text']: raise ValueError('unit absent from evidence')
        if hop['period'] != '2025H1' or chunk['report_period'] != hop['period']: raise ValueError('unexpected period')
        converted = number(hop['value']) * UNITS[hop['unit']]
        if converted != number(hop['value_yuan']) or converted != number(operand): raise ValueError('unit conversion mismatch')
        source_check(hop)
    expected = calculate(row)
    actual = number(row['answer_value']) if row['answer_type'] == 'number' else row['answer_value']
    if actual != expected: raise ValueError(f'answer mismatch: expected {expected}')
    return str(expected)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists(): raise FileExistsError(args.out)
    import fitz
    corpus = Path('data/candidates/docling/chunks.jsonl')
    chunks = {c['chunk_id']: c for c in map(json.loads, corpus.read_text().splitlines())}
    manifests = {m['sha256']:m for m in json.loads(Path('data/source_manifest.json').read_text())}
    pdfs = {}
    def source_check(hop):
        sha = hop['source_sha256']
        if sha not in pdfs:
            path = Path('data/raw_pdf') / manifests[sha]['filename']
            if hashlib.sha256(path.read_bytes()).hexdigest() != sha: raise ValueError('PDF hash mismatch')
            pdfs[sha] = fitz.open(path)
        text = re.sub(r'[\s,，]', '', pdfs[sha][hop['page']-1].get_text())
        if re.sub(r'[\s,，]', '', str(hop['value'])) not in text: raise ValueError('value absent from source page')
    rows = list(map(json.loads, args.input.read_text().splitlines()))
    ids = Counter(r['id'] for r in rows)
    questions = Counter(r['question'].strip() for r in rows)
    results = []
    for row in rows:
        try:
            if ids[row['id']] != 1 or questions[row['question'].strip()] != 1: raise ValueError('duplicate id/question')
            answer = validate_row(row, chunks, source_check)
            results.append({'id':row['id'], 'status':'RULE_PASSED', 'recomputed_answer':answer})
        except (KeyError, ValueError, ArithmeticError, IndexError, TypeError) as exc:
            results.append({'id':row.get('id'), 'status':'REJECTED', 'reason':str(exc)})
    report = {'input_sha256':hashlib.sha256(args.input.read_bytes()).hexdigest(),
              'corpus_sha256':hashlib.sha256(corpus.read_bytes()).hexdigest(),
              'total':len(rows), 'passed':sum(r['status']=='RULE_PASSED' for r in results),
              'type_counts':dict(Counter(r['type'] for r in rows)), 'results':results,
              'scope':'source hash, exact quote, source-page numeric presence, units and arithmetic; semantic/source-layout review required separately',
              'reachability':'NOT_RUN', 'independent_judge':'NOT_RUN', 'trajectory':'NOT_COLLECTED'}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open('x') as f: json.dump(report, f, ensure_ascii=False, indent=2)
    for pdf in pdfs.values(): pdf.close()
    print(json.dumps({k:v for k,v in report.items() if k!='results'}, ensure_ascii=False))
    raise SystemExit(0 if report['passed']==len(rows) and len(rows)==30 else 1)

if __name__ == '__main__': main()
