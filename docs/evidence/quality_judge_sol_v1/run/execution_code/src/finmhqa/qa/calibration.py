"""Content-bound human references and judge label comparison for Dev calibration."""
from collections import Counter
import hashlib
import json

DIMENSIONS=('numeric_grounding','unit_applicability','period','scope','citation_coverage','precision','abstention')
LABELS=('supported','missing','contradicted','uncertain','not_applicable')
BAD={'missing','contradicted'}


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def blank_review(case):
    return {'id':case['id'],'case_sha256':digest(case),'reviewer':'','reviewer_type':'human','reviewed_at':'',
            'labels':{d:None for d in DIMENSIONS},'rationale':{d:'' for d in DIMENSIONS},
            'evidence_ids':{d:[] for d in DIMENSIONS},'notes':''}


def validate_reviews(cases,reviews,*,human=False):
    by_id={c['id']:c for c in cases}
    if len(by_id)!=len(cases) or len({r['id'] for r in reviews})!=len(reviews):raise ValueError('duplicate IDs')
    if set(by_id)!={r['id'] for r in reviews}:raise ValueError('fixed review denominator changed')
    pending=[];complete=[]
    for r in reviews:
        case=by_id[r['id']]
        if r['case_sha256']!=digest(case):raise ValueError('review belongs to changed case')
        for field in ('labels','rationale','evidence_ids'):
            if set(r[field])!=set(DIMENSIONS):raise ValueError('dimension set changed')
        seen={c['chunk_id'] for o in case['observations'] for c in o['response'].get('results',[])}
        for d in DIMENSIONS:
            value=r['labels'][d]
            if value is not None and value not in LABELS:raise ValueError('invalid label')
            if not isinstance(r['rationale'][d],str):raise ValueError('invalid rationale')
            if not isinstance(r['evidence_ids'][d],list) or any(not isinstance(x,str) for x in r['evidence_ids'][d]):raise ValueError('invalid evidence IDs')
            if not set(r['evidence_ids'][d]).issubset(seen):raise ValueError('unobserved rationale citation')
            if value is not None and not r['rationale'][d].strip():raise ValueError('label requires rationale')
            if case['final']['status']=='answered' and d!='abstention' and value=='not_applicable':raise ValueError('answered dimensions cannot be skipped')
            if case['final']['status']=='answered' and d=='abstention' and value not in (None,'not_applicable'):raise ValueError('answered abstention must be not_applicable')
            if case['final']['status']=='insufficient' and d!='abstention' and value not in (None,'not_applicable'):raise ValueError('abstention is not a faithful answered example')
            if case['final']['status']=='insufficient' and d=='abstention' and value=='not_applicable':raise ValueError('must assess abstention')
        if any(v is None for v in r['labels'].values()):pending.append(r['id']);continue
        if not r['reviewer'].strip() or not r['reviewed_at'].strip():raise ValueError('completed review needs attribution and timestamp')
        if human and r['reviewer_type']!='human':raise ValueError('human reference required; assistant proposals are not human approval')
        complete.append(r)
    return pending,complete


def compare(cases,references,predictions=None):
    pending,complete=validate_reviews(cases,references,human=True)
    base={'scope':'development calibration only; not Train/Test release or a global Faithfulness score',
          'total_cases':len(cases),'reference_complete':len(complete),'reference_pending_ids':pending}
    if pending:return {**base,'status':'PENDING_HUMAN_REFERENCE','metrics':None}
    if predictions is None:return {**base,'status':'PENDING_JUDGE','metrics':None}
    judge_pending,_=validate_reviews(cases,predictions)
    if judge_pending:return {**base,'status':'PENDING_JUDGE','judge_pending_ids':judge_pending,'metrics':None}
    by_id={r['id']:r for r in predictions};metrics={};disagreements=[]
    for d in DIMENSIONS:
        pairs=[(r['labels'][d],by_id[r['id']]['labels'][d]) for r in references]
        # Uncertain references remain visible, but do not define adjudicated truth.
        eligible=[(a,b) for a,b in pairs if a not in ('uncertain','not_applicable')]
        bad=sum(a in BAD for a,b in eligible);supported=sum(a=='supported' for a,b in eligible)
        metrics[d]={'reference_uncertain':sum(a=='uncertain' for a,b in pairs),
            'reference_not_applicable':sum(a=='not_applicable' for a,b in pairs),'eligible':len(eligible),
            'exact_agreement':sum(a==b for a,b in eligible)/len(eligible) if eligible else None,
            'bad_reference_count':bad,'false_accept_count':sum(a in BAD and b=='supported' for a,b in eligible),
            'false_accept_rate':sum(a in BAD and b=='supported' for a,b in eligible)/bad if bad else None,
            'supported_reference_count':supported,'false_reject_count':sum(a=='supported' and b in BAD for a,b in eligible),
            'judge_uncertain':sum(b=='uncertain' for a,b in eligible),
            'confusion':dict(Counter(f'{a}->{b}' for a,b in pairs))}
        for r in references:
            if r['labels'][d]!=by_id[r['id']]['labels'][d]:disagreements.append({'id':r['id'],'dimension':d,'reference':r['labels'][d],'judge':by_id[r['id']]['labels'][d]})
    return {**base,'status':'CALIBRATION_REPORTED','metrics':metrics,'disagreements':disagreements,
            'release_approved':False,'caveat':'Reviewer attribution is an attestation, not authentication. No automatic threshold or training admission.'}
