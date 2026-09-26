# Docling 五页小样结果

## 结论

用户已于本轮明确批准下载模型并执行小样。5 页均完成离线 CPU 推理，共识别 8 张表，存在明确的结构改善；可作为后续复杂表格后端候选。但不能据此宣布 M1 全量验收通过，也不能信任所有原生单元格坐标。现有 `data/chunks.jsonl` 未替换。

## 实测

| 样本 | 表数 | 秒数 | 对照结论 |
|---|---:|---:|---|
| 美的 p56 | 2 | 20.17 | 本期/上年两层列名及公允价值表的完整列名恢复；含首次模型初始化 |
| 美的 p96 | 1 | 4.78 | 原基线漏检的完整利润表恢复，合并/公司四列分开；原页面有可见表线，纠正此前“无框”分类 |
| 茅台 p8 | 3 | 2.44 | 产品/渠道/地区分组保留，境外经销商减少数量仍为空 |
| 比亚迪 p65 | 1 | 2.09 | 无框利润表金额和括号负数保留；原生导出的日期行需适配 |
| 格力 p156 | 1 | 2.37 | 直接/间接持股列正确展开；不再把页框和标题吞入表体 |

共 31.856 秒，不包含安装下载时间；不是全量吞吐或并发性能基准。文档版本 Docling 2.126.0，docling-core 2.96.0，docling-ibm-models 4.0.2；TableFormer accurate，CPU 4 线程，关闭 OCR、远程服务、图片描述及其他增强。模型文件约 669 MB（官方下载器同时取回未用于本次推理的 ONNX 布局与 fast TableFormer 变体）。

## 验证范围与失败记录

- 11 个从原页面图像整理的事实锚点，逐行全列比较，全部通过；检查项固定保存在 `configs/docling_pilot_checks.json`，没有改变金额、符号、空值或期间来适配输出。仅规范空白字符。
- 456 个纯数值单元格（含附注编号等，不能全部称作金额）与独立 PyMuPDF 字符坐标比较：434 个一致，22 个不一致，全部位于美的 p96。
- 不一致项的值均在该页源文本中出现，但“页内出现”不能证明行列或 bbox 正确。22 项保留在 `midea-p96/numeric_audit.json` 和 `bbox_mismatch_followup.json`。未放宽坐标阈值来消除失败。
- 例如 `217,044` 的原生 bbox 截取到上一行的 `-`，`20,389,715` 的 bbox 混入上一行 `(260,126)`；这属于必须保留的定位缺陷。原页面视觉及独立行锚点支持这些值在目标行，但不修饰原生坐标。
- 已阅读全部 8 张表的导出内容，并结合 5 页原图检查代表性行列关系。未完成一个独立标注的、覆盖所有文本单元格的人工金标准；不能报告“全表 100% 正确”或把这些精挑的难例结果推广为 300 条随机审核通过率。
- 跨页表拼接、单位向后继承、完整章节链、全量 1,750 页回归仍未由此小样验证。

## 本轮实现

`src/finmhqa/ingest/docling_adapter.py` 提供不依赖 Docling runtime 的 JSON 适配层：按原始 rowspan/colspan 展开，合并已有列名文本；将被原模型放入正文的日期、附注、审计/重述说明保留在对应列上下文。保留原生 cells、cell_grid 与页码，不补金额或空值。3 个新增回归测试覆盖合并表头、期间说明、非法/重叠跨度；项目共 13 项测试通过。

`run_docling_pilot.py` 是独立模型运行入口，设定本地 artifacts_path、HF 离线环境、禁用远程服务，按物理 PDF 页码转换，核对原始 SHA256。模型依赖锁定在 `requirements-docling.lock.txt`，模型 revision 固定在 `configs/docling_models.json`，文件哈希在 `data/models/docling/provenance.json`。

## 复现与查看

```bash
uv venv --python 3.11 .venv-docling
uv pip install --python .venv-docling/bin/python -r requirements-docling.lock.txt
# 仅重建环境时需要联网预下载：
.venv-docling/bin/python scripts/download_docling_models.py
# 推理离线运行：
.venv-docling/bin/python scripts/run_docling_pilot.py
.venv/bin/python scripts/audit_docling_pilot.py
.venv/bin/python scripts/check_docling_facts.py
.venv/bin/python scripts/build_docling_review.py
.venv/bin/python -m pytest -q
```

可视对比：`data/qa/docling_pilot/review.html`。逐页包含原始 document.json、原生 Markdown/HTML、适配 views.json、run.json 和 numeric_audit.json。坐标核对失败不会被脚本当成异常中断，失败数是需要阅读的实验结果；事实锚点检查失败会返回非零。

## 后续实施边界

小样完成，Docling 可进入受控适配阶段，但应采用已验证的源 PDF 页码作为引用依据，原生单元格 bbox 必须携带未验证状态。下一步是将表头适配与单位/章节/跨页规则接入同一证据契约，再生成全量候选并进行固定 300 条审核；不能直接把本次小样视为对全量数据的批准或质量证明。M2/M3 模型和 API 决策尚未发生。
