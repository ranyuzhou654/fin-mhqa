"""Structural audit and deterministic review material; never substitutes for human QA."""
from collections import Counter, defaultdict
import argparse
import hashlib
import json
from pathlib import Path
import random
from transformers import AutoTokenizer


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--chunks',type=Path,default=Path('data/chunks.jsonl'))
    parser.add_argument('--qa-dir',type=Path,default=Path('data/qa'))
    args=parser.parse_args()
    path = args.chunks
    chunks = [json.loads(l) for l in path.read_text().splitlines()]
    records = json.loads(Path('data/source_manifest.json').read_text())
    expected = {r['company']:r for r in records}
    tokenizer = AutoTokenizer.from_pretrained('data/tokenizers/bge-m3',local_files_only=True,trust_remote_code=False)
    seen, errors, warnings = set(), [], Counter()
    groups = defaultdict(list)
    parents = {c['parent_table_id'] for c in chunks if c['chunk_type']=='table_md'}
    for c in chunks:
        if c['chunk_id'] in seen: errors.append(['duplicate_id',c['chunk_id']])
        seen.add(c['chunk_id'])
        r = expected[c['company']]
        if c['source_sha256'] != r['sha256'] or c['company_code'] != r['company_code']: errors.append(['source_mismatch',c['chunk_id']])
        if not 1 <= c['page_start'] <= c['page_end'] <= r['pages']: errors.append(['page_range',c['chunk_id']])
        actual = len(tokenizer.encode(c['text'].split('\n',1)[1],add_special_tokens=False))
        if actual != c['n_tokens']: errors.append(['token_count',c['chunk_id']])
        if c['chunk_type']=='text' and actual>450: errors.append(['text_limit',c['chunk_id']])
        if c['chunk_type']=='table_kv' and c['parent_table_id'] not in parents: errors.append(['orphan_kv',c['chunk_id']])
        if '列名未识别' in c['text']: warnings['unresolved_column_chunks'] += 1
        if c['chunk_type'].startswith('table') and not c['unit']: warnings['unit_unspecified_chunks'] += 1
        if c['section_path']=='正文': warnings['root_section_chunks'] += 1
        groups[c['company'],c['chunk_type']].append(c)
    rng=random.Random(42)
    samples=[]
    for key, values in sorted(groups.items()):
        for c in rng.sample(values,min(10,len(values))):
            samples.append(dict(c,breadcrumb_correct=None,table_correct=None,review_notes=''))
    qa=args.qa_dir;qa.mkdir(parents=True,exist_ok=True)
    sample_path=qa/'review_samples.jsonl'
    if sample_path.exists():
        prior=[json.loads(line) for line in sample_path.read_text().splitlines()]
        if any(c.get('breadcrumb_correct') is not None or c.get('table_correct') is not None or c.get('review_notes') for c in prior):
            raise ValueError('Refusing to overwrite annotated review samples; use a new QA directory')
    (qa/'review_samples.jsonl').write_text(''.join(json.dumps(c,ensure_ascii=False)+'\n' for c in samples),encoding='utf-8')
    report=dict(dataset=str(path),dataset_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),chunks=len(chunks),companies=len(expected),pages=sum(r['pages'] for r in records),
                structural_errors=errors,heuristic_warnings=dict(warnings),sample_count=len(samples),
                manual_review_status='pending',acceptance='NOT_ACCEPTED_PENDING_REVIEW',
                counts=[dict(company=k[0],chunk_type=k[1],count=len(v)) for k,v in sorted(groups.items())])
    (qa/'audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))
    if errors:raise SystemExit(1)

if __name__=='__main__':main()
