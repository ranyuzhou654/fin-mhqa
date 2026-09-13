"""Freeze v2 gold from source-only discovery and explicit reviewed decisions."""
import argparse
import json
from pathlib import Path
import pymupdf
from finmhqa.qa.equivalence import apply_review
from finmhqa.retrieval.artifacts import sha256
from discover_retrieval_equivalence import compact


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,default=Path('data/eval/retrieval_dev30.v2.bound.jsonl'))
    a=p.parse_args()
    if a.out.exists() or a.out.with_suffix('.summary.json').exists():raise FileExistsError(a.out)
    review_path=Path('configs/retrieval_equivalence_v2_review.json')
    review=json.loads(review_path.read_text())
    inputs=dict(query_source_sha256=Path('configs/retrieval_dev30.jsonl'),dataset_sha256=Path('data/candidates/docling/chunks.jsonl'),
        previous_bound_sha256=Path('data/eval/retrieval_dev30.bound.jsonl'),discovery_sha256=Path('data/eval/equivalence_v2/discovery.json'))
    for field,path in inputs.items():
        if sha256(path)!=review[field]:raise ValueError(f'Review input changed: {field}')
    records={r['company_code']:r for r in json.loads(Path('data/source_manifest.json').read_text())}
    pages={};verified=[]
    def check_source(q,c,d):
        record=records[q['company_code']]
        if q['company_code'] not in pages:
            pdf=Path('data/raw_pdf')/record['filename']
            if sha256(pdf)!=q['source_sha256']:raise ValueError('Original PDF changed')
            with pymupdf.open(pdf) as doc:pages[q['company_code']]=[compact(p.get_text()) for p in doc]
        image=Path(d['source_visual'])
        if not image.resolve().is_relative_to(Path('data/eval/equivalence_v2/visual').resolve()):raise ValueError('Unexpected visual path')
        if sha256(image)!=d['source_visual_sha256']:raise ValueError('Reviewed source render changed')
        text=pages[q['company_code']][d['source_page']-1]
        if not all(compact(term) in text for term in d['source_terms']):return False
        verified.append(dict(query_id=q['query_id'],chunk_id=c['chunk_id'],page=d['source_page'],pdf_sha256=q['source_sha256']))
        return True
    previous=[json.loads(l) for l in inputs['previous_bound_sha256'].read_text().splitlines()]
    chunks=[json.loads(l) for l in inputs['dataset_sha256'].read_text().splitlines()]
    discovery=json.loads(inputs['discovery_sha256'].read_text())
    results=apply_review(previous,chunks,discovery,review,check_source)
    a.out.parent.mkdir(parents=True,exist_ok=True)
    with a.out.open('x') as f:
        for r in results:f.write(json.dumps(r,ensure_ascii=False)+'\n')
    summary=dict(label_version=review['label_version'],status='FROZEN',queries=len(results),bound=len(results),missing=[],
        **{key:sha256(path) for key,path in inputs.items()},bound_sha256=sha256(a.out),review_sha256=sha256(review_path),
        review_path=str(review_path),gold_count=sum(len(r['gold_chunk_ids']) for r in results),additional_gold_count=len(verified),
        source_verified_additions=verified,retrieval_evaluated=False)
    a.out.with_suffix('.summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:summary[k] for k in ('label_version','queries','gold_count','additional_gold_count','bound_sha256')},indent=2))

if __name__=='__main__':main()
