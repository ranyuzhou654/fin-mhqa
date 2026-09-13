"""Apply explicit source reviews without deriving relevance from retrieval ranks."""
import hashlib
import json


def chunk_digest(chunk):
    return hashlib.sha256(json.dumps(chunk,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def apply_review(previous,chunks,discovery,review,check_source):
    if review.get('status')!='SOURCE_REVIEW_COMPLETE' or review.get('label_version')!='source_equivalence_v2':
        raise ValueError('Equivalence review is not complete')
    by_id={c['chunk_id']:c for c in chunks}
    if len(by_id)!=len(chunks):raise ValueError('Duplicate chunk IDs')
    questions={q['query_id']:q for q in previous}
    rows={r['query_id']:r for r in review['queries']}
    found={r['query_id']:r for r in discovery['queries']}
    if len(rows)!=len(review['queries']) or set(rows)!=set(questions) or set(found)!=set(questions):
        raise ValueError('Review must cover every frozen question')
    result=[]
    for qid,q in questions.items():
        row=rows[qid];decisions=row['decisions'];ids=[d['chunk_id'] for d in decisions]
        if len(ids)!=len(set(ids)) or set(ids)!=set(found[qid]['candidate_ids']):
            raise ValueError('Every discovered candidate needs exactly one decision')
        if row['source_pages']!=found[qid]['source_pages']:raise ValueError('Source search coverage changed')
        old=set(q['gold_chunk_ids']);gold=set(old)
        if not old<=set(ids):raise ValueError('Original gold omitted from review')
        for d in decisions:
            c=by_id[d['chunk_id']]
            if c['company_code']!=q['company_code'] or c['source_sha256']!=q['source_sha256']:
                raise ValueError('Candidate subject or source differs')
            if d['source_sha256']!=c['source_sha256'] or d['source_page']!=c['page_start'] or d['chunk_sha256']!=chunk_digest(c):
                raise ValueError('Reviewed evidence changed')
            if not d.get('reason','').strip():raise ValueError('Missing review rationale')
            if c['chunk_id'] in old:
                if d['decision']!='retain_v1':raise ValueError('Original gold cannot be rewritten')
            elif d['decision']=='accept':
                if q.get('expected_unit') and c.get('unit')!=q['expected_unit']:
                    raise ValueError('Added revenue evidence must expose the original unit')
                if not d.get('source_terms') or not check_source(q,c,d):raise ValueError('Missing source verification')
                gold.add(c['chunk_id'])
            elif d['decision']!='reject':raise ValueError('Invalid additional candidate decision')
        result.append(dict(q,gold_chunk_ids=sorted(gold),label_status='source_equivalence_reviewed',
                           label_version='source_equivalence_v2'))
    return result
