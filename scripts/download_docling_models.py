"""Reproduce the user-approved local layout/table artifacts at pinned revisions."""
import json
from pathlib import Path
from huggingface_hub import snapshot_download

if __name__=='__main__':
    config=json.loads(Path('configs/docling_models.json').read_text())
    for model in config['models']:
        snapshot_download(repo_id=model['repo_id'],revision=model['revision'],
                          local_dir=Path('data/models/docling')/model['repo_id'].replace('/','--'))
