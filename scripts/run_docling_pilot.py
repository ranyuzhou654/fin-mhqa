"""Run the approved five-page CPU-only Docling pilot using downloaded local models."""
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import time

# Fail closed on missing artifacts. No downloads or remote services during inference.
os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'
os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'


def main():
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, TableStructureOptions, TableFormerMode
    from docling.datamodel.accelerator_options import AcceleratorOptions, AcceleratorDevice
    from docling.document_converter import DocumentConverter, PdfFormatOption
    config = json.loads(Path('configs/docling_pilot.json').read_text())
    out = Path('data/qa/docling_pilot')
    out.mkdir(parents=True,exist_ok=True)
    artifacts = Path('data/models/docling').resolve()
    options = PdfPipelineOptions(artifacts_path=artifacts,do_ocr=False,do_table_structure=True,
        enable_remote_services=False,allow_external_plugins=False,
        accelerator_options=AcceleratorOptions(device=AcceleratorDevice.CPU,num_threads=4),
        table_structure_options=TableStructureOptions(mode=TableFormerMode.ACCURATE,do_cell_matching=True))
    converter = DocumentConverter(format_options={InputFormat.PDF:PdfFormatOption(pipeline_options=options)})
    sources = {r['filename']:r for r in json.loads(Path('data/source_manifest.json').read_text())}
    summary = []
    for sample in config['samples']:
        source = Path('data/raw_pdf')/sample['filename']
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        if digest != sources[sample['filename']]['sha256']:
            raise ValueError(f'Source hash mismatch: {source}')
        dest = out/sample['id'];dest.mkdir(exist_ok=True)
        print('START',sample['id'],flush=True)
        start=time.monotonic()
        result=converter.convert(source,page_range=(sample['page'],sample['page']))
        elapsed=time.monotonic()-start
        if result.status.value != 'success':
            raise RuntimeError(f'{sample["id"]}: {result.status} {result.errors}')
        document=result.document
        (dest/'document.json').write_text(document.model_dump_json(indent=2),encoding='utf-8')
        (dest/'document.md').write_text(document.export_to_markdown(),encoding='utf-8')
        tables=[]
        for i,table in enumerate(document.tables):
            md=table.export_to_markdown(doc=document)
            (dest/f'table-{i}.md').write_text(md,encoding='utf-8')
            (dest/f'table-{i}.html').write_text(table.export_to_html(doc=document),encoding='utf-8')
            tables.append(dict(index=i,rows=table.data.num_rows,cols=table.data.num_cols,
                               cells=len(table.data.table_cells),provenance=[p.model_dump(mode='json') for p in table.prov]))
        record=dict(**sample,sha256=digest,seconds=round(elapsed,3),status=result.status.value,
                    document_pages=list(document.pages),tables=tables,
                    versions={name:version(name) for name in ['docling','docling-core','docling-ibm-models','torch','transformers']})
        (dest/'run.json').write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        summary.append(record)
        (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print('DONE',sample['id'],round(elapsed,2),[(t['rows'],t['cols']) for t in tables],flush=True)

if __name__=='__main__':main()
