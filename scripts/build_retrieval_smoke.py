"""Construct a labeled experimental smoke corpus without admitting M1 data."""
import json
from pathlib import Path
import random
from finmhqa.retrieval.artifacts import read_chunks,sha256


def main():
    config=json.loads(Path('configs/retrieval_smoke.json').read_text())
    source=Path('data/candidates/docling/chunks.jsonl')
    chunks=read_chunks(source)
    bound=[json.loads(l) for l in Path('data/eval/retrieval_dev30.bound.jsonl').read_text().splitlines()]
    queries=[q for q in bound if q['query_id'] in config['query_ids']]
    if len(queries)!=len(config['query_ids']) or any(q['dataset_sha256']!=sha256(source) for q in queries):
        raise ValueError('Smoke query source differs')
    selected={i for q in queries for i in q['gold_chunk_ids']}
    rng=random.Random(config['seed'])
    for company in sorted({c['company'] for c in chunks}):
        options=[c for c in chunks if c['company']==company and c['chunk_type']=='text' and c['chunk_id'] not in selected]
        selected.update(c['chunk_id'] for c in rng.sample(options,config['distractor_texts_per_company']))
    parents={(c['company'],c['source_sha256'],c['parent_table_id'],c['part']) for c in chunks if c['chunk_id'] in selected and c['chunk_type']=='table_kv'}
    selected.update(c['chunk_id'] for c in chunks if c['chunk_type']=='table_md' and (c['company'],c['source_sha256'],c['parent_table_id'],c['part']) in parents)
    root=Path('data/experiments/retrieval_smoke');root.mkdir(parents=True,exist_ok=True)
    with (root/'chunks.jsonl').open('x') as stream:
        for c in chunks:
            if c['chunk_id'] in selected:stream.write(json.dumps(c,ensure_ascii=False)+'\n')
    smoke_sha=sha256(root/'chunks.jsonl')
    (root/'queries.jsonl').write_text(''.join(json.dumps(dict(q,source_dataset_sha256=q['dataset_sha256'],dataset_sha256=smoke_sha,evaluation_scope='smoke_preselected_positives'),ensure_ascii=False)+'\n' for q in queries))
    report=dict(count=len(selected),source_sha256=sha256(source),smoke_sha256=sha256(root/'chunks.jsonl'),query_ids=config['query_ids'],purpose=config['purpose'])
    (root/'selection.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
