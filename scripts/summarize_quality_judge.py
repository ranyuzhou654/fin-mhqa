"""Archive one complete Judge run and describe judgments without reference accuracy."""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import shutil
from finmhqa.qa.calibration import DIMENSIONS,validate_reviews
from score_quality_calibration import load_packet
from audit_quality_judge import audit_run


def main():
    p=argparse.ArgumentParser();p.add_argument('--packet',type=Path,required=True);p.add_argument('--run',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    if a.out.exists():raise FileExistsError(a.out)
    cases,packet=load_packet(a.packet);manifest=json.loads((a.run/'manifest.json').read_text())
    audit=audit_run(a.packet,a.run)
    if not all(audit['current_execution_matches'].values()):raise ValueError('execution source changed before archive')
    reviews=json.loads((a.run/'predictions.json').read_text());pending,_=validate_reviews(cases,reviews)
    meta={r['id']:r for r in packet['cases']};by_id={c['id']:c for c in cases};usage=Counter();rows=[]
    for r in reviews:
        result=json.loads((a.run/r['id']/'result.json').read_text());usage.update(result['usage'] or {})
        rows.append({'id':r['id'],'group':meta[r['id']]['group'],'variant':meta[r['id']]['variant'],
            'answer_status':by_id[r['id']]['final']['status'],'labels':r['labels'],'notes':r['notes'],'error':result['error']})
    answered=[r for r in rows if r['answer_status']=='answered'];refused=[r for r in rows if r['answer_status']=='insufficient']
    controls={ 'unsupported_number':'numeric_grounding','missing_header_citation':'citation_coverage','wrong_period':'period',
               'wrong_scope':'scope','unseen_citation':'citation_coverage','premature_rounding':'precision'}
    summary={'scope':'Judge opinions only; reference labels pending; no accuracy/false-accept/Faithfulness score',
        'total':len(cases),'completed':len(cases)-len(pending),'errors':[r for r in rows if r['error']],
        'answered_cases':len(answered),'refused_cases':len(refused),
        'answered_six_supported':sum(all(r['labels'][d]=='supported' for d in DIMENSIONS if d!='abstention') for r in answered),
        'label_counts':{d:dict(Counter(r['labels'][d] for r in rows)) for d in DIMENSIONS},
        'controlled_edit_observations':[{'id':r['id'],'variant':r['variant'],'target_dimension':controls[r['variant']],
              'judge_label':r['labels'][controls[r['variant']]],'is_human_reference':False} for r in rows if r['variant'] in controls],
        'usage':dict(usage),'rows':rows,'reference_complete':0,'metrics':None,'release_approved':False}
    a.out.mkdir(parents=True)
    for name in ['manifest.json','predictions.json','run_status.json']:shutil.copy2(a.run/name,a.out/name)
    for name,obj in [('summary.json',summary),('audit.json',audit)]: (a.out/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n')
    for c in cases:
        target=a.out/'cases'/c['id'];target.mkdir(parents=True)
        for name in ['result.json','call/events.jsonl','call/decision.json','call/usage.json','call/schema.json']:
            source=a.run/c['id']/name
            if source.exists():shutil.copy2(source,target/Path(name).name)
    files={str(p.relative_to(a.run)):hashlib.sha256(p.read_bytes()).hexdigest() for p in a.run.rglob('*') if p.is_file()}
    (a.out/'runtime_hashes.json').write_text(json.dumps(files,indent=2)+'\n')
    for name in manifest['code_sha256']:
        source=Path(name).resolve();relative=source.relative_to(Path.cwd().resolve());target=a.out/'execution_code'/relative
        target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
    for source in [Path(__file__),Path('scripts/audit_quality_judge.py')]:
        target=a.out/'evaluation_code'/source.name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
    lines=['# Sol Judge 实际判定（待人工裁定）','','每条使用独立上下文，仍有同模型偏差。以下是模型意见，不是已核实质量标签。人工参考仍为空。','']
    for r in reviews:
        provenance=meta[r['id']]
        lines += ['## '+r['id'],'',f"来源组：{provenance['group']}；类型：{provenance['variant']}。",'',by_id[r['id']]['question'],'',
                  f"[无Judge标签的证据页]({os.path.relpath(a.packet / 'review_cases' / (r['id']+'.md'),a.out)})",'', '| 维度 | Judge标签 | 理由 / 证据 |','| --- | --- | --- |']
        for d in DIMENSIONS:
            text=r['rationale'][d].replace('|',' / ').replace('\n',' ')
            lines.append(f"| {d} | {r['labels'][d]} | {text}；{', '.join(r['evidence_ids'][d])} |")
        lines += ['',r['notes'],'']
    (a.out/'REVIEW.md').write_text('\n'.join(lines))
    print(json.dumps({k:summary[k] for k in ['total','completed','answered_six_supported','label_counts','controlled_edit_observations','usage']},ensure_ascii=False))

if __name__=='__main__':main()
