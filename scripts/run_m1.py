"""Resumable report extraction; per-report page cache retains source evidence."""
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import time
from finmhqa.ingest.extract import extract_pdf, Page, RawTable
from finmhqa.ingest.chunk import chunk_pages


def process(record):
    from transformers import AutoTokenizer
    started = time.monotonic()
    path = Path('data/raw_pdf') / record['filename']
    if hashlib.sha256(path.read_bytes()).hexdigest() != record['sha256']:
        raise ValueError(f'Source hash mismatch: {path}')
    cache = Path('data/extracted') / (path.stem+'.json')
    cache.parent.mkdir(exist_ok=True)
    cache_version = 4
    cached = json.loads(cache.read_text()) if cache.exists() else {}
    if cached.get('extractor_version') == cache_version:
        payload = json.loads(cache.read_text())
        if payload['source_sha256'] != record['sha256']:
            raise ValueError('Stale page cache')
        pages=[]
        for p in payload['pages']:
            p['tables']=[RawTable(**t) for t in p['tables']]
            pages.append(Page(**p))
    else:
        pages = extract_pdf(path,record['company'])
        cache.write_text(json.dumps(dict(extractor_version=cache_version,source_sha256=record['sha256'],pages=[asdict(p) for p in pages]),ensure_ascii=False),encoding='utf-8')
    tokenizer=AutoTokenizer.from_pretrained('data/tokenizers/bge-m3',local_files_only=True,trust_remote_code=False)
    count=lambda text:len(tokenizer.encode(text,add_special_tokens=False))
    chunks=chunk_pages(record['company'],pages,count,source_sha256=record['sha256'],company_code=record['company_code'])
    print(record['company'],len(pages),'pages',sum(len(p.tables) for p in pages),'tables',len(chunks),'chunks',round(time.monotonic()-started,1),'sec',flush=True)
    return record['company_code'],chunks


def main():
    records=json.loads(Path('data/source_manifest.json').read_text())
    outputs={}
    with ProcessPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(process,r) for r in records]
        for f in as_completed(futures):
            code,chunks=f.result()
            outputs[code]=chunks
    out=Path('data/chunks.jsonl')
    if out.exists():
        out=Path('data/chunks.candidate.jsonl')
    with out.open('x',encoding='utf-8') as stream:
        for code in sorted(outputs):
            for c in outputs[code]:
                stream.write(json.dumps(c,ensure_ascii=False)+'\n')
    print('OUTPUT',out,flush=True)

if __name__=='__main__':
    main()
