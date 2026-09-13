"""Archive compact audit evidence while large replay inputs stay under data/."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil


def archive(run,out):
    if out.exists():raise FileExistsError(out)
    manifest=json.loads((run/'manifest.json').read_text())
    sources=[]
    for name,sha in manifest.get('code_sha256',{}).items():
        p=Path(name).resolve()
        relative=p.relative_to(Path.cwd().resolve())
        if hashlib.sha256(p.read_bytes()).hexdigest()!=sha:raise ValueError('archive before changing execution code: '+name)
        sources.append((p,relative))
    out.mkdir(parents=True)
    shutil.copy2(run/'manifest.json',out/'manifest.json')
    if (run/'health_after.json').exists():shutil.copy2(run/'health_after.json',out/'health_after.json')
    for folder in sorted(run.glob('*/*')):
        if not folder.is_dir() or not (folder/'trajectory.jsonl').exists():continue
        arm=folder.parent.name;target=out/'trajectories'/arm;target.mkdir(parents=True,exist_ok=True)
        shutil.copy2(folder/'trajectory.jsonl',target/f'{folder.name}.jsonl')
        events=[]
        for step in sorted(folder.glob('step_*')):
            for line in (step/'events.jsonl').read_text().splitlines():events.append({'step':int(step.name.split('_')[1]),'event':json.loads(line)})
        target=out/'model_events'/arm;target.mkdir(parents=True,exist_ok=True)
        (target/f'{folder.name}.jsonl').write_text(''.join(json.dumps(e,ensure_ascii=False)+'\n' for e in events))
    files={str(p.relative_to(run)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(run.rglob('*')) if p.is_file()}
    (out/'runtime_hashes.json').write_text(json.dumps(files,indent=2))
    for p,relative in sources:
        target=out/'execution_code'/relative;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,target)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args();archive(a.run,a.out)
