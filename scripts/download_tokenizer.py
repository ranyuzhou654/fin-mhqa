"""Download only user-approved bge-m3 tokenizer artifacts at a pinned revision."""
import hashlib
import json
from pathlib import Path
import requests

REVISION = '5617a9f61b028005a4858fdac845db406aefb181'
FILES = ['tokenizer.json', 'tokenizer_config.json', 'special_tokens_map.json', 'sentencepiece.bpe.model']

def main():
    dest = Path('data/tokenizers/bge-m3')
    dest.mkdir(parents=True, exist_ok=True)
    records = []
    for name in FILES:
        url = f'https://huggingface.co/BAAI/bge-m3/resolve/{REVISION}/{name}'
        path = dest / name
        if not path.exists():
            r = requests.get(url, timeout=120)
            r.raise_for_status()
            path.write_bytes(r.content)
        records.append(dict(file=name, sha256=hashlib.sha256(path.read_bytes()).hexdigest(), bytes=path.stat().st_size, url=url))
        print(name, path.stat().st_size, flush=True)
    (dest/'provenance.json').write_text(json.dumps(dict(repo='BAAI/bge-m3', revision=REVISION, files=records), indent=2)+'\n')

if __name__ == '__main__':
    main()
