"""Build an exclusive candidate dataset from a complete, hash-verified native cache."""
import argparse
import hashlib
import json
from pathlib import Path
from transformers import AutoTokenizer
from finmhqa.ingest.chunk import chunk_pages
from finmhqa.ingest.docling_adapter import pages_from_native


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native-root',required=True,type=Path)
    parser.add_argument('--out',default='data/candidates/docling/chunks.jsonl',type=Path)
    args=parser.parse_args()
    if args.out.exists():raise FileExistsError(args.out)
    tokenizer=AutoTokenizer.from_pretrained('data/tokenizers/bge-m3',local_files_only=True,trust_remote_code=False)
    count=lambda text:len(tokenizer.encode(text,add_special_tokens=False))
    records=json.loads(Path('data/source_manifest.json').read_text())
    args.out.parent.mkdir(parents=True,exist_ok=True)
    temporary=args.out.with_suffix('.building')
    counts=[]
    with temporary.open('x',encoding='utf-8') as stream:
        for record in records:
            batches=sorted((args.native_root/record['company_code']).glob('*.json'))
            pages=pages_from_native(Path('data/raw_pdf')/record['filename'],record['company'],batches,expected_sha256=record['sha256'])
            chunks=chunk_pages(record['company'],pages,count,source_sha256=record['sha256'],company_code=record['company_code'])
            for chunk in chunks:stream.write(json.dumps(chunk,ensure_ascii=False)+'\n')
            counts.append(dict(company=record['company'],pages=len(pages),tables=sum(len(p.tables) for p in pages),chunks=len(chunks),
                rejected_tables=[dict(page=p.page_no,**issue) for p in pages for issue in p.extraction_issues]))
            print(counts[-1],flush=True)
    # Hard link publishes atomically and fails if the destination appeared meanwhile.
    import os
    os.link(temporary,args.out)
    temporary.unlink()
    manifest=dict(native_root=str(args.native_root),output=str(args.out),sha256=hashlib.sha256(args.out.read_bytes()).hexdigest(),counts=counts,acceptance='NOT_ACCEPTED_PENDING_REVIEW')
    args.out.with_suffix('.manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')

if __name__=='__main__':main()
