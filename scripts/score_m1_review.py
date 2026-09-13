"""Score explicitly annotated M1 samples bound to an unchanged audit and corpus."""
import argparse
import hashlib
import json
from pathlib import Path
from finmhqa.qa.review import score_review


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--qa-dir',required=True,type=Path)
    p.add_argument('--review',required=True,type=Path)
    a=p.parse_args()
    audit=json.loads((a.qa_dir/'audit.json').read_text())
    dataset=Path(audit['dataset'])
    if hashlib.sha256(dataset.read_bytes()).hexdigest()!=audit['dataset_sha256']:
        raise ValueError('Dataset changed after audit')
    fixed=[json.loads(l) for l in (a.qa_dir/'review_samples.jsonl').read_text().splitlines()]
    if hashlib.sha256((a.qa_dir/'review_samples.jsonl').read_bytes()).hexdigest()!=audit['sample_sha256']:
        raise ValueError('Fixed samples changed; annotate a separate copy')
    reviewed=[json.loads(l) for l in a.review.read_text().splitlines()]
    chunks=[json.loads(l) for l in dataset.read_text().splitlines()]
    result=score_review(chunks,reviewed,[s['chunk_id'] for s in fixed])
    result.update(dataset_sha256=audit['dataset_sha256'],review_sha256=hashlib.sha256(a.review.read_bytes()).hexdigest(),structural_errors=audit['structural_errors'])
    if audit['structural_errors']:result['status']='FAILED_STRUCTURE'
    target=a.qa_dir/'review_result.json'
    target.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if result['status']!='PASSED':raise SystemExit(1)

if __name__=='__main__':main()
