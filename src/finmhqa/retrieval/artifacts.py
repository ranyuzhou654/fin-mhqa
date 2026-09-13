import hashlib
import json
from pathlib import Path


def sha256(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(8*1024*1024),b''):digest.update(block)
    return digest.hexdigest()


def read_chunks(path):
    chunks=[json.loads(line) for line in Path(path).read_text().splitlines()]
    if not chunks or len({c['chunk_id'] for c in chunks})!=len(chunks):
        raise ValueError('Empty corpus or duplicate chunk IDs')
    for c in chunks:
        if not c['text'].strip() or not c['company'] or not c['source_sha256'] or not 1<=c['page_start']<=c['page_end']:
            raise ValueError('Incomplete evidence metadata')
    parents={(c['company'],c['source_sha256'],c['parent_table_id'],c['part']) for c in chunks if c['chunk_type']=='table_md'}
    if any((c['company'],c['source_sha256'],c['parent_table_id'],c['part']) not in parents for c in chunks if c['chunk_type']=='table_kv'):
        raise ValueError('Orphan table KV part')
    return chunks


def admission(chunks_path, out_dir, qa_dir=None, *, experimental=False, reviewed_samples=None):
    digest=sha256(chunks_path)
    if experimental:
        if not Path(out_dir).resolve().is_relative_to(Path('data/experiments').resolve()):
            raise ValueError('Experimental indexes must remain under data/experiments')
        return dict(status='EXPERIMENTAL_UNREVIEWED',dataset_sha256=digest)
    if qa_dir is None:raise ValueError('Formal index requires M1 QA evidence')
    qa_dir=Path(qa_dir)
    audit=json.loads((qa_dir/'audit.json').read_text())
    review=json.loads((qa_dir/'review_result.json').read_text())
    if review.get('status')!='PASSED' or audit.get('structural_errors'):
        raise ValueError('M1 acceptance required: review is not PASSED')
    if audit['dataset_sha256']!=digest or review['dataset_sha256']!=digest:
        raise ValueError('M1 acceptance belongs to another dataset')
    fixed=qa_dir/'review_samples.jsonl'
    if sha256(fixed)!=audit['sample_sha256'] or reviewed_samples is None or sha256(reviewed_samples)!=review['review_sha256']:
        raise ValueError('M1 review evidence changed or missing')
    from finmhqa.qa.review import score_review
    ids=[json.loads(l)['chunk_id'] for l in fixed.read_text().splitlines()]
    labels=[json.loads(l) for l in Path(reviewed_samples).read_text().splitlines()]
    if score_review(read_chunks(chunks_path),labels,ids)['status']!='PASSED':
        raise ValueError('M1 labels no longer meet acceptance')
    return dict(status='ACCEPTED',dataset_sha256=digest,review_sha256=sha256(qa_dir/'review_result.json'),audit_sha256=sha256(qa_dir/'audit.json'))


def verify_model_bundle(root, config_path='configs/retrieval_models.json'):
    root=Path(root)
    provenance=json.loads((root/'provenance.json').read_text())
    if provenance['config_sha256']!=sha256(config_path):raise ValueError('Model pin configuration changed')
    config=json.loads(Path(config_path).read_text())
    expected={(m['repo_id'],m['revision'],f['name']):f for m in config['models'] for f in m['files']}
    observed=set()
    for item in provenance['files']:
        key=(item['repo_id'],item['revision'],Path(item['path']).name)
        if key not in expected:raise ValueError('Unapproved model artifact')
        path=root/item['repo_id'].split('/')[1]/key[2]
        if path.stat().st_size!=expected[key]['bytes'] or sha256(path)!=item['sha256']:
            raise ValueError(f'Model artifact integrity failure: {path}')
        if expected[key].get('sha256') and item['sha256']!=expected[key]['sha256']:
            raise ValueError('Model weight differs from upstream hash')
        observed.add(key)
    if observed!=set(expected):raise ValueError('Missing model artifact verification')
    return sha256(root/'provenance.json')
