"""One ephemeral Sol call per frozen case; no reference labels or source variants sent."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import time
from finmhqa.agent.codex_teacher import CodexTeacher
from finmhqa.qa.calibration import blank_review,digest
from finmhqa.qa.judge import schema_for,system_prompt,bind
from score_quality_calibration import load_packet


def main():
    p=argparse.ArgumentParser();p.add_argument('--packet',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--workers',type=int,choices=[1,2,4],default=4);a=p.parse_args()
    if a.out.exists():raise FileExistsError(a.out)
    cases,packet=load_packet(a.packet)
    inputs={c['id']:json.loads((a.packet/'judge_inputs'/(c['id']+'.json')).read_text()) for c in cases}
    rubric=json.loads((a.packet/'rubric.json').read_text())['prompt']
    for c in cases:
        if inputs[c['id']]!={'rubric':rubric,'case':c}:raise ValueError('judge input differs from frozen case/rubric')
    a.out.mkdir(parents=True)
    binary='/Applications/ChatGPT.app/Contents/Resources/codex'
    teacher=CodexTeacher(binary,a.out/'empty_cwd')
    files=[Path(__file__),Path('src/finmhqa/qa/judge.py'),Path('src/finmhqa/qa/calibration.py'),Path('src/finmhqa/agent/codex_teacher.py'),Path('scripts/score_quality_calibration.py')]
    manifest={'packet_sha256':hashlib.sha256((a.packet/'manifest.json').read_bytes()).hexdigest(),'case_ids':[c['id'] for c in cases],
        'case_sha256':{c['id']:digest(c) for c in cases},'model':'gpt-5.6-sol','reasoning':'low','workers':a.workers,
        'mode':'one ephemeral independent context per case; same model bias remains','attempts_per_case':1,
        'references_sent':False,'prior_verifier_sent':False,'gold_sent':False,
        'code_sha256':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
        'cli_sha256':hashlib.sha256(Path(binary).read_bytes()).hexdigest()}
    (a.out/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    def run(c):
        folder=a.out/c['id'];folder.mkdir();start=time.monotonic();error=None;usage=None;decision=None
        timestamp=datetime.now(timezone.utc).isoformat()
        review=blank_review(c);review.update(reviewer_type='model',reviewer=manifest['model'],reviewed_at=timestamp)
        try:
            decision,usage=teacher.decide(c['question'],inputs[c['id']]['case'],folder/'call',
                system=system_prompt(rubric),output_schema=schema_for(c))
            review=bind(c,decision,timestamp)
        except Exception as exc:error=f'{type(exc).__name__}: {exc}';review['notes']='RUN_FAILED: '+error
        result={'id':c['id'],'case_sha256':digest(c),'review':review,'error':error,'usage':usage,'seconds':time.monotonic()-start}
        (folder/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({'id':c['id'],'error':error,'seconds':round(result['seconds'],2)},ensure_ascii=False),flush=True)
        return result
    with ThreadPoolExecutor(max_workers=a.workers) as pool:results=list(pool.map(run,cases))
    (a.out/'predictions.json').write_text(json.dumps([r['review'] for r in results],ensure_ascii=False,indent=2)+'\n')
    (a.out/'run_status.json').write_text(json.dumps({'total':len(results),'completed':sum(not r['error'] for r in results),'errors':[{'id':r['id'],'error':r['error']} for r in results if r['error']],
        'quality_accuracy':None,'human_reference':'pending'},ensure_ascii=False,indent=2)+'\n')

if __name__=='__main__':main()
