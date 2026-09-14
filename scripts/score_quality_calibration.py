"""Offline only: verify frozen packet, then compare explicit human/Judge labels."""
import argparse
import hashlib
import json
from pathlib import Path
from finmhqa.qa.calibration import compare,digest


def load_packet(root):
    manifest=json.loads((root/'manifest.json').read_text())
    for name,sha in manifest['files_sha256'].items():
        p=(root/name).resolve()
        if not p.is_relative_to(root.resolve()) or hashlib.sha256(p.read_bytes()).hexdigest()!=sha:
            raise ValueError('frozen packet changed: '+name)
    cases=[json.loads(l) for l in (root/'cases.jsonl').read_text().splitlines()]
    expected={r['id']:r['case_sha256'] for r in manifest['cases']}
    if len(cases)!=manifest['case_count'] or {c['id']:digest(c) for c in cases}!=expected:raise ValueError('case manifest mismatch')
    return cases,manifest


def main():
    p=argparse.ArgumentParser();p.add_argument('--packet',type=Path,required=True);p.add_argument('--reference',type=Path,required=True)
    p.add_argument('--predictions',type=Path);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    if a.out.exists():raise FileExistsError(a.out)
    cases,manifest=load_packet(a.packet)
    references=json.loads(a.reference.read_text());predictions=json.loads(a.predictions.read_text()) if a.predictions else None
    report=compare(cases,references,predictions)
    report['packet_sha256']=hashlib.sha256((a.packet/'manifest.json').read_bytes()).hexdigest()
    report['reference_sha256']=hashlib.sha256(a.reference.read_bytes()).hexdigest()
    report['predictions_sha256']=hashlib.sha256(a.predictions.read_bytes()).hexdigest() if a.predictions else None
    report['groups']=len({r['group'] for r in manifest['cases']})
    report['sampling_caveat']='Purposefully selected Dev failures and paired edits, not prevalence or independent held-out estimate.'
    a.out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:report[k] for k in ['status','total_cases','reference_complete','metrics']},ensure_ascii=False))

if __name__=='__main__':main()
