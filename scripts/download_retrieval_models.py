"""Download only approved, revision-pinned inference files and verify their hashes."""
import hashlib
import json
from pathlib import Path
from huggingface_hub import hf_hub_download


def main():
    config=json.loads(Path('configs/retrieval_models.json').read_text())
    root=Path('data/models/retrieval');root.mkdir(parents=True,exist_ok=True)
    records=[]
    for model in config['models']:
        target=root/model['name']
        for item in model['files']:
            path=Path(hf_hub_download(repo_id=model['repo_id'],revision=model['revision'],filename=item['name'],local_dir=target))
            digest=hashlib.sha256(path.read_bytes()).hexdigest()
            if path.stat().st_size != item['bytes'] or (item.get('sha256') and digest != item['sha256']):
                raise ValueError(f'Model artifact differs from pinned source: {path}')
            records.append(dict(path=path.as_posix(),sha256=digest,bytes=path.stat().st_size,repo_id=model['repo_id'],revision=model['revision']))
            print('VERIFIED',path.name,path.stat().st_size,flush=True)
    (root/'provenance.json').write_text(json.dumps(dict(config_sha256=hashlib.sha256(Path('configs/retrieval_models.json').read_bytes()).hexdigest(),files=records),indent=2)+'\n')

if __name__=='__main__':main()
