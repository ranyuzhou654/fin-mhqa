"""Build a local review page with source renders and unmodified model exports."""
import html
import json
from pathlib import Path
from finmhqa.ingest.chunk import to_md


def main():
    root=Path('data/qa/docling_pilot')
    config=json.loads(Path('configs/docling_pilot.json').read_text())
    summary={r['id']:r for r in json.loads((root/'summary.json').read_text())}
    sections=[]
    for sample in config['samples']:
        key=sample['id'];record=summary[key]
        baseline=json.loads((Path('data/extracted')/sample['filename'].replace('.pdf','.json')).read_text())['pages'][sample['page']-1]
        raw='\n\n'.join(to_md(t['rows']) for t in baseline['tables']) or '该页未生成可靠的结构化表；内容保留为正文。'
        tables=''.join((root/key/f'table-{i}.html').read_text() for i in range(len(record['tables'])))
        views=json.loads((root/key/'views.json').read_text())
        headers='\n'.join(f'表 {i+1}: '+ ' | '.join(v['headers']) for i,v in enumerate(views))
        imagepath=key+'.png' if (root/(key+'.png')).exists() else '../'+key+'.png'
        sections.append(f'''<section id="{key}"><h2>{html.escape(key)} · {sample['issue']} · {record['seconds']:.2f}s</h2>
        <p>PDF 物理页码 {sample['page']}。以下为实际输出；无通过率推断。</p>
        <div class="columns"><div><h3>原页面</h3><a href="{imagepath}"><img src="{imagepath}" alt="{key} 原页面"></a></div>
        <div><h3>Docling 原生表格</h3><div class="tables">{tables}</div></div></div>
        <details><summary>确定性适配后的列名（含期间、重述说明）</summary><pre>{html.escape(headers)}</pre></details>
        <details><summary>PyMuPDF / pdfplumber 基线表格</summary><pre>{html.escape(raw)}</pre></details>
        <p><a href="{key}/document.json">Docling JSON</a> · <a href="{key}/numeric_audit.json">逐数值坐标检查</a> · <a href="{key}/views.json">适配视图</a></p></section>''')
    nav=' · '.join(f'<a href="#{x["id"]}">{x["id"]}</a>' for x in config['samples'])
    document='''<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Docling 五页对比</title>
    <style>body{font-family:system-ui,sans-serif;color:#172033;background:#f5f7fa;margin:0;padding:32px}main{max-width:1800px;margin:auto}section{background:white;border:1px solid #dce1e9;border-radius:12px;padding:24px;margin:30px 0}.columns{display:grid;grid-template-columns:1fr 1fr;gap:24px}img{width:100%}.tables{overflow:auto;max-height:1000px}table{border-collapse:collapse;font-size:12px;margin-bottom:24px}td,th{border:1px solid #c5ced9;padding:6px;white-space:nowrap}th{background:#edf2fa}pre{white-space:pre-wrap;overflow:auto;font-size:12px}a{color:#195dcc}details{padding:16px 0}summary{cursor:pointer}@media(max-width:1000px){.columns{grid-template-columns:1fr}body{padding:12px}}</style>
    <main><h1>Docling 五页对比</h1><p>8 张表；11 个图像转录事实锚点全部通过。456 个数值单元格中，434 个与原 PDF 坐标区域文字一致，22 个坐标匹配失败，均保留记录。该结果不能代替全量 M1 验收。</p>
    <p>美的第 96 页实际有可见表线，原先的“无框”判断已纠正。适配器只整合已有表头文本，不生成金额。原生单元格 bbox 不作为已验证的精确定位。</p><nav>'''+nav+'</nav>'+''.join(sections)+'</main></html>'
    (root/'review.html').write_text(document,encoding='utf-8')

if __name__=='__main__':main()
