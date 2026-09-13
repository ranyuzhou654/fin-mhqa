"""Source check and freeze gold separately from teacher-visible questions."""
import argparse
from collections import Counter
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path
import re
import pymupdf
from finmhqa.agent.pilot_tools import decimal, FACTORS

def expected(row):
    hops={h['hop_id']:h for h in row['hops']}
    if len(hops)!=len(row['hops']):raise ValueError('duplicate hop id')
    d=row['derivation'];selected=None
    if d['operation']=='select_then_read':
        if row['type']!='bridge' or row['bridge_kind']!='conditional_entity_selection':raise ValueError('bridge type mismatch')
        selection=[hops[k] for k in d['selection_hop_ids']]
        if len(selection)!=2 or len({h['company_code'] for h in selection})!=2:raise ValueError('two selector companies required')
        if len({h['metric'] for h in selection})!=1:raise ValueError('selector metric mismatch')
        if d['selector'] not in ('argmax','argmin'):raise ValueError('unknown selector')
        nums=[decimal(h['value_yuan']) for h in selection]
        if nums[0]==nums[1]:raise ValueError('ambiguous selector tie')
        winner=selection[nums.index((max if d['selector']=='argmax' else min)(nums))]
        target=hops[d['target_hop_id']]
        if target['company_code']!=winner['company_code']:raise ValueError('wrong selected company')
        if target['metric']==winner['metric'] or target['chunk_id'] in {h['chunk_id'] for h in selection}:raise ValueError('not a new evidence dependency')
        selected=winner['company'];value=decimal(target['value_yuan'])
    else:
        operands=[decimal(hops[k]['value_yuan']) for k in d['operand_hop_ids']]
        if len(operands)!=2:raise ValueError('pilot requires binary comparison/aggregation')
        if d['operation']=='difference':value=operands[0]-operands[1]
        elif d['operation']=='sum':value=sum(operands)
        else:raise ValueError('unknown operation')
    result=(value/FACTORS[row['answer_unit']]).quantize(Decimal(1).scaleb(-row['round_digits']),rounding=ROUND_HALF_UP)
    return str(result),selected

def main():
    p=argparse.ArgumentParser();p.add_argument('--gold',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    if a.out.exists():raise FileExistsError(a.out)
    corpus=Path('data/candidates/docling/chunks.jsonl');chunks={r['chunk_id']:r for r in map(json.loads,corpus.read_text().splitlines())}
    manifest={m['sha256']:m for m in json.loads(Path('data/source_manifest.json').read_text())};pdfs={}
    rows=list(map(json.loads,a.gold.read_text().splitlines()));checked=[]
    if len(rows)!=12 or len({r['id'] for r in rows})!=12:raise ValueError('pilot needs 12 unique cases')
    for row in rows:
        if row['split']!='dev_pilot_only' or row['generator_model']!='gpt-5.6-sol':raise ValueError('invalid provenance/split')
        if len({h['source_sha256'] for h in row['hops']})<2:raise ValueError('requires multiple reports')
        for h in row['hops']:
            c=chunks[h['chunk_id']]
            for key in ['company','company_code','source_sha256']:
                if h[key]!=c[key]:raise ValueError('source metadata mismatch')
            if not c['page_start']<=h['page']<=c['page_end']:raise ValueError('page mismatch')
            if h['period']!=c['report_period'] or h['period']!='2025H1':raise ValueError('period mismatch')
            if not h['quote'] or h['quote'] not in c['text']:raise ValueError('quote not exact')
            compact=lambda s:re.sub(r'[\s,，]','',str(s))
            if compact(h['value']) not in compact(h['quote']) or compact(h['metric']) not in compact(h['quote']):raise ValueError('metric/value absent')
            if h['unit'] not in c['text']:raise ValueError('unit absent')
            if decimal(h['value'])*FACTORS[h['unit']]!=decimal(h['value_yuan']):raise ValueError('unit conversion error')
            sha=h['source_sha256']
            if sha not in pdfs:
                f=Path('data/raw_pdf')/manifest[sha]['filename']
                if hashlib.sha256(f.read_bytes()).hexdigest()!=sha:raise ValueError('PDF changed')
                pdfs[sha]=pymupdf.open(f)
            if compact(h['value']) not in compact(pdfs[sha][h['page']-1].get_text()):raise ValueError('value absent from source page')
        value,selected=expected(row)
        if decimal(value)!=decimal(row['answer_value']):raise ValueError('gold arithmetic error')
        checked.append({'id':row['id'],'recomputed_answer':value,'selected_company':selected,'status':'RULE_PASSED'})
    a.out.mkdir(parents=True)
    questions=''.join(json.dumps({'id':r['id'],'question':r['question']},ensure_ascii=False)+'\n' for r in rows)
    (a.out/'questions.jsonl').write_text(questions)
    report={'status':'FROZEN','gold_sha256':hashlib.sha256(a.gold.read_bytes()).hexdigest(),'questions_sha256':hashlib.sha256(questions.encode()).hexdigest(),'corpus_sha256':hashlib.sha256(corpus.read_bytes()).hexdigest(),'results':checked,'types':dict(Counter(r['type'] for r in rows)),'gold_isolation':'execution script only reads questions.jsonl; gold remains offline','semantic_review':'generator source review; not independent human or judge'}
    (a.out/'freeze.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps({'status':report['status'],'count':len(rows),'types':report['types']}))
    for pdf in pdfs.values():pdf.close()

if __name__=='__main__':main()
