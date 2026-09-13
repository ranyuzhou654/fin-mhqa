# fin-mhqa

按根目录《02_金融多跳问答系统_实现文档》逐阶段落地，设计依据见《01_金融多跳问答系统_设计方案》。当前已收集 10 份真实半年报并完成 **M1 首轮全量处理**；质量验收未通过，M2–M8 尚未启动。

## 本地环境

```bash
uv venv --python 3.11
uv pip install --python .venv/bin/python -r requirements-m1.lock.txt
uv pip install --python .venv/bin/python --no-deps -e .
.venv/bin/python -m pytest -q
```

依赖与模型分开：安装 Python 依赖不下载模型；生产入口只读取已确认的本地 tokenizer。

## M1 操作

1. 已收集 10 家公司 2025 年半年报 PDF，位于 `data/raw_pdf/`，共 1,750 页；见 [来源清单](docs/DATA_SOURCES.md)。
2. `configs/companies.json` 和 `configs/company_metadata.json` 已填充文件名、简称、代码与行业。
3. 已经用户确认并下载 bge-m3 tokenizer，全部运行仅读取本地文件：

```bash
.venv/bin/python -m finmhqa.ingest.chunk \
  --pdf_dir data/raw_pdf --company_map configs/companies.json \
  --tokenizer-path data/tokenizers/bge-m3 --out data/chunks.jsonl
.venv/bin/python tests/inspect_chunks.py data/chunks.jsonl --out data/review_samples.jsonl
```

输出为 text、table_md、table_kv 三类证据，含章节、页码范围、来源 SHA256、确定性 chunk ID 和父表 ID。现有输出不覆盖。没有文本层或表格抽取失败会报错，不静默跳过。

## 验收边界

离线测试使用注入的字符计数器检查算法，不代表 bge-m3 token 长度已验收。真实数据需确认每公司三类各抽 10 条、breadcrumb/表格正确率至少 95%，并核对总体数量目标 6k–10k；不能为满足数量而重复数据。详见 [实施进度](docs/IMPLEMENTATION_STATUS.md)。

## 可复现脚本

- `scripts/collect_reports.py`：巨潮公开披露下载及 PDF 身份、页数、SHA256 核对。
- `scripts/download_tokenizer.py`：固定 revision，仅下载 tokenizer 文件。
- `scripts/run_m1.py`：两个本地工作进程，按报告缓存抽取结果；原 PDF 哈希不匹配则报错。
- `scripts/audit_m1.py`：全量结构检查、真实 tokenizer 复算、固定 300 条审核样本。

最新结果在 `data/chunks.jsonl`，质量报告在 `data/qa/audit.json`，历史迭代在 `data/runs/`。数据文件默认被 Git 忽略，原始文档和生成物均未提交。下一决策见 [复杂表格小样试验](docs/M1_PARSER_DECISION.md)。

## Docling 小样

已批准并完成 5 页试验，模型隔离在 `.venv-docling/`，本地权重位于 `data/models/docling/`。结果、复现命令和坐标限制见 [小样报告](docs/DOCLING_PILOT_RESULTS.md)。本轮未替换 `data/chunks.jsonl`，不能将小样结果等同于 M1 全量验收。
