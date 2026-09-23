# fin-mhqa

面向上市公司财报的**多跳证据问答 Agent**：在 10 家公司 2025 年半年报（1,750 页）上，Agent 通过混合检索、受控上下文读取和确定性计算回答跨公司、跨报表的多跳问题，每个答案都附带可回溯到原文的引用和完整的可审计轨迹。

**项目范围（2026-09-23 调整）**：本项目定位为 Agent 项目，重点是架构设计与可证明的改进。原方案中的学生模型 SFT 与 GRPO 训练已移出范围，设计文档中预设的训练指标不再作为目标。见[范围调整](docs/PROJECT_GAP_AND_ACCEPTANCE.md)。

## 当前状态

| 模块 | 状态 |
| --- | --- |
| 数据底座 | Docling 解析得到 9,322 条可追溯证据块（正文、表格 Markdown、表格键值）。见[全量结果](docs/DOCLING_CORPUS_RESULTS.md) |
| 混合检索 | FAISS + BM25 + RRF + BGE 重排，本地 HTTP 服务。30 题开发集 hybrid Hit@5 为 29/30（v2 标签，v1 失败结果保留）。见[v2 验收](docs/M2_V2_RESULTS.md) |
| Agent | LangGraph Planner–Executor–Verifier–Synthesizer，结构化重规划、预算控制、续表与单位说明的受控读取、确定性计算、原样发布已核验的答案。见[M3 设计](docs/AGENT_M3_DESIGN.md)、[M3.1 设计](docs/AGENT_M31_DESIGN.md) |
| Agent 模型 | 现用 DeepSeek（deepseek-v4.1-flash，OpenAI 兼容接口），dev20 为 20/20；Sol 的 rc1 为 19/20。见[teacher_v1](docs/TEACHER_V1.md)、[DeepSeek](docs/TEACHER_V2_DEEPSEEK.md) |
| 质量评判 | Sol、DeepSeek、Claude 三个模型的 Judge 对比，未经人工校准。见[对比](docs/JUDGE_COMPARISON_V1.md) |
| 评测集 | **进行中**：dev20 已饱和（flat 基线也是 20/20），正在建设更难的冻结评测集。见[设计](docs/BENCHMARK_DESIGN.md) |

历史阶段、失败记录及所有数值的来源见[实施进度](docs/IMPLEMENTATION_STATUS.md)。

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

算法测试使用注入的计数器；全量审计另用本地 bge-m3 tokenizer 复算实际 token 数。每公司三类各抽 10 条的审核已按用户确认代录通过；总体数量目标为 6k–10k，不能为满足数量而重复数据。详见 [实施进度](docs/IMPLEMENTATION_STATUS.md)。

## 可复现脚本

- `scripts/collect_reports.py`：巨潮公开披露下载及 PDF 身份、页数、SHA256 核对。
- `scripts/download_tokenizer.py`：固定 revision，仅下载 tokenizer 文件。
- `scripts/run_m1.py`：两个本地工作进程，按报告缓存抽取结果；原 PDF 哈希不匹配则报错。
- `scripts/audit_m1.py`：全量结构检查、真实 tokenizer 复算、固定 300 条审核样本。

基础解析对照保存在 `data/chunks.jsonl`，其质量报告在 `data/qa/audit.json`，历史迭代在 `data/runs/`。最新 Docling 候选在 `data/candidates/docling/chunks.jsonl`，质量报告在 `data/qa/docling_corpus/`。大体积数据默认被 Git 忽略，小型来源与验证摘要提交在 `docs/evidence/`。已批准的模型与后续决策见 [M2 决策说明](docs/M2_DECISION.md)。

## Docling 小样

已批准并完成 5 页试验，模型隔离在 `.venv-docling/`，本地权重位于 `data/models/docling/`。结果、复现命令和坐标限制见 [小样报告](docs/DOCLING_PILOT_RESULTS.md)。本轮未替换 `data/chunks.jsonl`，不能将小样结果等同于 M1 全量验收。

## Docling 全量候选流程

使用已批准的小样模型离线处理，默认每 25 页缓存一次，可用同一命令断点续跑：

```bash
.venv-docling/bin/python scripts/run_docling_corpus.py
.venv/bin/python scripts/build_docling_corpus.py --native-root data/docling_native/59cdcef7571d
.venv/bin/python scripts/audit_m1.py --chunks data/candidates/docling/chunks.jsonl --qa-dir data/qa/docling_corpus
```

缓存目录随模型配置及依赖版本变化，应以转换脚本输出的 `cache_root` 为准；同一缓存使用固定的 25 页批次。构建器核对全部源文件哈希、配置一致性、页覆盖与重复页，排除单元格跨度异常的结构化表格并保留原页正文与错误标记。原生 JSON 保存完整单元格跨度和坐标，chunk 中的来源引用包含批次路径，坐标状态为未验证。未知表头不猜测，跨页表格仅在表头与版面条件均匹配时合并。候选集独立输出；结构审计不代替 300 条源页人工核验。

全量候选的事实回归与人工审核评分：

```bash
.venv/bin/python scripts/check_docling_corpus_facts.py
.venv/bin/python scripts/audit_docling_corpus_cells.py --native-root data/docling_native/59cdcef7571d
# 复制 review_samples.jsonl 为 reviewed.jsonl，按原始 PDF 逐条填写布尔标签和说明。
.venv/bin/python scripts/score_m1_review.py --qa-dir data/qa/docling_corpus --review data/qa/docling_corpus/reviewed.jsonl
```

固定样本保持不变，标签写入单独副本。评分同时检查每公司三类各 10 条、正文/表格 breadcrumb、表格内容与联合正确率均至少 95%、总数 6k–10k，以及结构审计零错误；未填标签会返回待审核，篡改证据或替换样本会报错。数值单元格坐标一致性是独立诊断，不充当事实正确率。

30 条源页检索开发问题在 `configs/retrieval_dev30.jsonl`。运行 `.venv/bin/python scripts/bind_retrieval_dev.py` 可在检索开始前固定候选证据；当前 30/30 已绑定，已运行正式全量检索；绑定率不代表检索命中率。

## M2 本地检索

两套模型已获批并完成本地推理及 HTTP 联调。正式索引已完成，v1 验收失败保留，v2 源页等价标签验收通过；18 条实验索引保留作历史联调证据。结果、真实故障修复和限制见 [M2 结果](docs/M2_RESULTS.md)。

检索环境与 M1、Docling 分开；模型推理通过本地子进程运行，避免 macOS 上 Torch 与 FAISS 的 OpenMP 冲突。

```bash
uv venv .venv-retrieval --python 3.11
uv pip install --python .venv-retrieval/bin/python -r requirements-retrieval.lock.txt
uv pip install --python .venv-retrieval/bin/python --no-deps -e .
.venv-retrieval/bin/python scripts/download_retrieval_models.py
.venv-retrieval/bin/python scripts/probe_retrieval_models.py
.venv-retrieval/bin/python -m pytest tests/test_retrieval.py -q
```

下载器固定模型版本并验证文件；实际推理仅访问本地文件。新目录可用以下命令复现实验；已有数据或索引会拒绝覆盖。当前检索实验已完成，服务已停止。

```bash
.venv-retrieval/bin/python scripts/build_retrieval_smoke.py
.venv-retrieval/bin/python -m finmhqa.retrieval.build_index \
  --chunks data/experiments/retrieval_smoke/chunks.jsonl \
  --out data/experiments/retrieval_smoke/index --experimental --batch-size 2
FINMHQA_INDEX=data/experiments/retrieval_smoke/index FINMHQA_EXPERIMENTAL=1 FINMHQA_BATCH_SIZE=2 \
  .venv-retrieval/bin/python -m uvicorn finmhqa.retrieval.server:create_app \
  --factory --host 127.0.0.1 --port 8101
```

服务就绪后，在另一个终端运行。评测输出路径须尚不存在：

```bash
.venv-retrieval/bin/python scripts/evaluate_retrieval.py \
  --url http://127.0.0.1:8101 \
  --queries data/experiments/retrieval_smoke/queries.jsonl \
  --out data/qa/retrieval/smoke_http_recheck.json --n-cand 8
```

`POST /search` 接受 `query`、`mode`（dense/bm25/hybrid）、`top_k`（默认 5）、`n_cand`（默认 30）、可选 `company` 和 `expand_parent`。`GET /health` 显示数据规模、审核状态与模型来源哈希。主结果与附加父表分开计数，截断和原始质量标记保留。

**下面是正式流程。本次 index/ 已创建，重复构建会拒绝覆盖；重复评测请更换输出文件名：**

```bash
.venv-retrieval/bin/python -m finmhqa.retrieval.build_index \
  --chunks data/candidates/docling/chunks.jsonl --out index \
  --qa-dir data/qa/docling_corpus --reviewed-samples data/qa/docling_corpus/reviewed.jsonl
.venv-retrieval/bin/python -m uvicorn finmhqa.retrieval.server:create_app \
  --factory --host 127.0.0.1 --port 8100
# 服务启动后在另一终端评测：
.venv-retrieval/bin/python scripts/evaluate_retrieval.py \
  --queries data/eval/retrieval_dev30.v2.bound.jsonl --out data/eval/retrieval_dev30.v2.recheck.json
```

正式构建会复算固定样本评分并核对数据、审核与索引哈希；服务默认拒绝实验索引。评测仅在正式数据、原始 30 题及其冻结绑定和默认候选数均匹配时产生正式验收判定；v2 另核对注册版本与源页审核哈希。v1 原绑定保留在 `data/eval/retrieval_dev30.bound.jsonl`，可指定该文件复跑原标签。标签发现、源页审核和冻结流程见 [v2 标签说明](docs/RETRIEVAL_LABELS_V2.md)。若 M1 修订改变候选，须先重新审核并绑定 gold 证据。

完整目标与当前实现差距、后续数据隔离及实验验收要求见 [项目差距与验收补充](docs/PROJECT_GAP_AND_ACCEPTANCE.md)。原方案中尚未运行的合成和训练数字不代表项目实测结果；训练部分已移出范围。

已使用内置 `gpt-5.6-sol` 完成 [30条开发QA小样](docs/SYNTH_PILOT_SOL_RESULTS.md)，可在 [逐题清单](docs/SYNTH_PILOT_SOL_REVIEW.md) 查看；这不是Agent轨迹，也不是正式评测集。
