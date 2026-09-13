"""Resumable, offline CPU Docling conversion of the approved report corpus."""
import argparse
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import time

os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'
os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'


def main():
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, TableStructureOptions, TableFormerMode
    from docling.datamodel.accelerator_options import AcceleratorOptions, AcceleratorDevice
    from docling.document_converter import DocumentConverter, PdfFormatOption
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch-pages',type=int,default=25)
    parser.add_argument('--company-code')
    args=parser.parse_args()
    if args.batch_pages < 1:parser.error('batch-pages must be positive')
    versions={n:version(n) for n in ['docling','docling-core','docling-ibm-models','torch','transformers']}
    settings=dict(versions=versions,ocr=False,table_mode='accurate',device='cpu',threads=4,
                  models=json.loads(Path('configs/docling_models.json').read_text()))
    signature=hashlib.sha256(json.dumps(settings,sort_keys=True).encode()).hexdigest()
    root=Path('data/docling_native')/signature[:12]
    root.mkdir(parents=True,exist_ok=True)
    (root/'settings.json').write_text(json.dumps(settings,indent=2)+'\n')
    options=PdfPipelineOptions(artifacts_path=Path('data/models/docling').resolve(),do_ocr=False,
        do_table_structure=True,enable_remote_services=False,allow_external_plugins=False,
        accelerator_options=AcceleratorOptions(device=AcceleratorDevice.CPU,num_threads=4),
        table_structure_options=TableStructureOptions(mode=TableFormerMode.ACCURATE,do_cell_matching=True))
    converter=DocumentConverter(format_options={InputFormat.PDF:PdfFormatOption(pipeline_options=options)})
    records=json.loads(Path('data/source_manifest.json').read_text())
    if args.company_code and not any(r['company_code']==args.company_code for r in records):
        raise ValueError('Unknown company code')
    progress_name = f'progress-{args.company_code}.json' if args.company_code else 'progress.json'
    summary_name = f'summary-{args.company_code}.json' if args.company_code else 'summary.json'
    failures=[]; completed=0
    for record in records:
        if args.company_code and record['company_code']!=args.company_code:continue
        source=Path('data/raw_pdf')/record['filename']
        if hashlib.sha256(source.read_bytes()).hexdigest()!=record['sha256']:
            raise ValueError(f'Source hash mismatch: {source}')
        folder=root/record['company_code'];folder.mkdir(exist_ok=True)
        for start in range(1,record['pages']+1,args.batch_pages):
            end=min(record['pages'],start+args.batch_pages-1)
            target=folder/f'{start:04d}-{end:04d}.json'
            if target.exists():
                cached=json.loads(target.read_text())
                if cached['source_sha256']!=record['sha256'] or cached['settings_sha256']!=signature:
                    raise ValueError(f'Stale cache: {target}')
                completed+=end-start+1
                print('CACHED',record['company'],start,end,flush=True)
                continue
            began=time.monotonic()
            print('START',record['company'],start,end,flush=True)
            try:
                result=converter.convert(source,page_range=(start,end))
                if result.status.value!='success':
                    raise RuntimeError(f'{result.status}: {result.errors}')
                actual=set(result.document.pages)
                if actual!=set(range(start,end+1)):
                    raise ValueError(f'Page coverage differs: {sorted(actual)}')
                payload=dict(source_sha256=record['sha256'],settings_sha256=signature,
                    filename=record['filename'],page_start=start,page_end=end,
                    seconds=round(time.monotonic()-began,3),document=result.document.model_dump(mode='json'))
                temporary=target.with_suffix('.tmp')
                temporary.write_text(json.dumps(payload,ensure_ascii=False)+'\n',encoding='utf-8')
                temporary.replace(target)
                completed+=end-start+1
                print('DONE',record['company'],start,end,payload['seconds'],'sec',flush=True)
            except Exception as exc:
                failure=dict(company=record['company'],page_start=start,page_end=end,error=str(exc))
                failures.append(failure)
                print('FAILED',failure,flush=True)
            (root/progress_name).write_text(json.dumps(dict(completed_pages=completed,failures=failures),ensure_ascii=False,indent=2)+'\n')
    summary=dict(cache_root=str(root),settings_sha256=signature,completed_pages=completed,failures=failures)
    (root/summary_name).write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(summary,ensure_ascii=False),flush=True)
    if failures:raise SystemExit(1)

if __name__=='__main__':main()
