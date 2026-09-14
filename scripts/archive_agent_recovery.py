"""Archive declared synthetic fault records separately from normal runs."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil


def archive(run,out):
    if out.exists():raise FileExistsError(out)
    manifest=json.loads((run/'manifest.json').read_text());driver=Path('scripts/run_agent_recovery.py')
    if hashlib.sha256(driver.read_bytes()).hexdigest()!=manifest['code_sha256']:raise ValueError('recovery driver changed before archive')
    sources=[]
    for name,sha in manifest.get('execution_sha256',{}).items():
        p=Path(name).resolve();relative=p.relative_to(Path.cwd().resolve())
        if hashlib.sha256(p.read_bytes()).hexdigest()!=sha:raise ValueError('execution changed before archive: '+name)
        sources.append((p,relative))
    out.mkdir(parents=True)
    for name in ['manifest.json','health_after.json']:shutil.copy2(run/name,out/name)
    (out/'trajectories').mkdir();(out/'model_events').mkdir();(out/'execution_code').mkdir()
    for name in manifest['scenarios']:
        case=run/name;shutil.copy2(case/'trajectory.jsonl',out/'trajectories'/f'{name}.jsonl')
        events=[]
        for step in sorted(case.glob('step_*')):
            for line in (step/'events.jsonl').read_text().splitlines():events.append({'step':int(step.name.split('_')[1]),'event':json.loads(line)})
        (out/'model_events'/f'{name}.jsonl').write_text(''.join(json.dumps(e,ensure_ascii=False)+'\n' for e in events))
    for p in [driver,Path('src/finmhqa/agent/graph.py'),Path('src/finmhqa/agent/graph_contracts.py')]:shutil.copy2(p,out/'execution_code'/p.name)
    for p,relative in sources:
        target=out/'execution_code'/relative;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,target)
    hashes={str(p.relative_to(run)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(run.rglob('*')) if p.is_file()}
    (out/'runtime_hashes.json').write_text(json.dumps(hashes,indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args();archive(a.run,a.out)
