"""Evaluate a running local retrieval API; primary Top-5 excludes attachments."""
import argparse
import json
from pathlib import Path
import time
import httpx
from finmhqa.retrieval.artifacts import sha256


def binding_snapshot(queries_path,queries):
    """Validate frozen semantics and the registered v2 review before and after a run."""
    path=Path(queries_path);binding_path=path.with_suffix('.summary.json')
    binding=json.loads(binding_path.read_text()) if binding_path.exists() else {}
    canonical=Path('configs/retrieval_dev30.jsonl')
    source=[json.loads(l) for l in canonical.read_text().splitlines()]
    by_id={q['query_id']:q for q in queries}
    versions={q.get('label_version','reference_page_v1') for q in queries}
    label_version=next(iter(versions)) if len(versions)==1 else 'mixed'
    semantics=(len(source)==len(queries) and {q['query_id'] for q in source}==set(by_id)
        and all(all(by_id[q['query_id']].get(k)==v for k,v in q.items() if k!='label_status') for q in source))
    verified=(binding.get('bound_sha256')==sha256(path)
        and binding.get('query_source_sha256')==sha256(canonical) and semantics)
    registry_hash=None;review_hash=None
    if label_version=='source_equivalence_v2':
        registry_path=Path('configs/retrieval_label_versions.json')
        registry=json.loads(registry_path.read_text()) if registry_path.exists() else {}
        registered=registry.get(label_version,{})
        review_path=Path(binding.get('review_path','__missing_review__'))
        review_hash=sha256(review_path) if review_path.is_file() else None
        registry_hash=sha256(registry_path) if registry_path.exists() else None
        verified=bool(verified and registered and binding.get('label_version')==label_version
            and binding.get('status')=='FROZEN' and review_hash==registered.get('review_sha256')
            and all(binding.get(k)==registered.get(k) for k in ('bound_sha256','query_source_sha256','dataset_sha256','review_sha256','review_path')))
    elif label_version!='reference_page_v1':verified=False
    return dict(verified=verified,label_version=label_version,dataset_sha256=binding.get('dataset_sha256'),
        query_sha256=sha256(path),binding_sha256=sha256(binding_path) if binding_path.exists() else None,
        registry_sha256=registry_hash,review_sha256=review_hash)


def evaluate(url,queries_path,out,*,n_cand=30):
    queries_path=Path(queries_path);out=Path(out)
    if out.exists():raise FileExistsError(out)
    queries=[json.loads(l) for l in queries_path.read_text().splitlines()]
    if not queries or len({q['query_id'] for q in queries})!=len(queries):raise ValueError('Empty or duplicate queries')
    frozen_start=binding_snapshot(queries_path,queries)
    results=[]
    out.parent.mkdir(parents=True,exist_ok=True)
    journal_path=out.with_suffix('.requests.jsonl')
    # Exclusive creation preserves prior partial attempts, including HTTP failures.
    with journal_path.open('x') as journal, httpx.Client(base_url=url,timeout=300,trust_env=False) as client:
        response=client.get('/health');response.raise_for_status();health=response.json()
        digest=health['admission']['dataset_sha256']
        if any(q['dataset_sha256']!=digest or not q['gold_chunk_ids'] for q in queries):raise ValueError('Query evidence binding differs from index')
        for mode in ('dense','bm25','hybrid'):
            for query in queries:
                start=time.monotonic()
                response=client.post('/search',json=dict(query=query['query'],mode=mode,top_k=5,n_cand=n_cand))
                journal.write(json.dumps(dict(query_id=query['query_id'],mode=mode,
                    seconds=time.monotonic()-start,status_code=response.status_code,
                    response_body=response.text),ensure_ascii=False)+'\n')
                journal.flush()
                response.raise_for_status();payload=response.json()
                if payload['dataset_sha256']!=digest:raise ValueError('Index changed during evaluation')
                primary=[r for r in payload['results'] if not r['attached']][:5]
                ids=[r['chunk_id'] for r in primary]
                if len(ids)!=len(set(ids)):raise ValueError('Duplicate primary results')
                hit=bool(set(ids)&set(query['gold_chunk_ids']))
                item=dict(query_id=query['query_id'],mode=mode,hit_at_5=hit,seconds=time.monotonic()-start,results=payload['results'])
                results.append(item);print(mode,query['query_id'],'hit' if hit else 'MISS',round(item['seconds'],3),flush=True)
    by_mode={mode:dict(hits=sum(r['hit_at_5'] for r in results if r['mode']==mode),total=len(queries),hit_at_5=sum(r['hit_at_5'] for r in results if r['mode']==mode)/len(queries)) for mode in ('dense','bm25','hybrid')}
    frozen_end=binding_snapshot(queries_path,queries)
    fixed_binding=(frozen_start==frozen_end and frozen_end['verified'] and frozen_end['dataset_sha256']==digest)
    eligible=health['admission']['status']=='ACCEPTED' and fixed_binding and len(queries)==30 and n_cand==30 and all(q.get('evaluation_scope')!='smoke_preselected_positives' for q in queries)
    report=dict(api=url,health=health,query_file_sha256=sha256(queries_path),n_cand=n_cand,by_mode=by_mode,
                fixed_binding_verified=fixed_binding,acceptance_eligible=eligible,acceptance='PASSED' if eligible and by_mode['hybrid']['hit_at_5']>.85 else ('FAILED' if eligible else 'DIAGNOSTIC_ONLY'),results=results)
    report.update(label_version=frozen_end['label_version'],binding_snapshot=frozen_end,request_journal=str(journal_path),request_journal_sha256=sha256(journal_path))
    with out.open('x') as stream:stream.write(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--url',default='http://127.0.0.1:8100')
    p.add_argument('--queries',default='data/eval/retrieval_dev30.bound.jsonl')
    p.add_argument('--out',required=True)
    p.add_argument('--n-cand',type=int,default=30)
    a=p.parse_args()
    report=evaluate(a.url,a.queries,a.out,n_cand=a.n_cand)
    print(json.dumps(dict(by_mode=report['by_mode'],acceptance=report['acceptance']),ensure_ascii=False,indent=2))
    if report['acceptance']=='FAILED':raise SystemExit(1)

if __name__=='__main__':main()
