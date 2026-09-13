# M2 全量索引与冻结开发集验收

**结论：正式索引构建成功，M2 检索验收 FAILED，未进入 M3。**

本次在用户确认的 M1 审核底座上运行本地两套模型，构建 9,322 条、1024 维 FAISS 与 BM25 索引。运行冻结的 30 道问题，每题分别执行 dense、bm25、hybrid，共 90 次真实 HTTP 查询。全部请求完成；评测进程退出码 1 表示未达到验收阈值，不是接口崩溃。

## 正式结果

| 模式（均经 reranker） | Top-5 命中 | 命中率 | 单次请求耗时中位数 |
|---|---:|---:|---:|
| Dense | 20/30 | 66.67% | 11.84 秒 |
| BM25 | 16/30 | 53.33% | 9.42 秒 |
| Hybrid / RRF | 23/30 | 76.67% | 11.00 秒 |

验收条件为 hybrid Top-5 **>85%**，30 题至少需要命中 26 题。本次只有 23 题命中，不能宣称 M2 通过。这里是固定证据 chunk ID 命中率，不是生成答案正确率，也不是独立测试集成绩。原始问题、gold、阈值均未调整；不同页相同数值未自动计为正确，附加父表未计入主结果 Top-5。

配置：CPU float32，模型输入长度 1024，batch size 4，候选数 30，Top-K 5，RRF k=60，父表展开开启；查询未额外传入 gold 公司字段作筛选。两套固定模型及运行时与此前联调一致。90 次串行请求合计约 17.8 分钟，耗时仅代表本机本次运行，不是生产吞吐或并发测量。

## 索引与数据边界

- 正式索引：`index/`，准入 `ACCEPTED`，8 个索引构件的哈希均核验通过。
- 数据：`data/candidates/docling/chunks.jsonl`，SHA256 `729608cf7488364acbe3646f218f8e156cd6df3f64031223fe8929f5cf7368e2`。原始基础解析 `data/chunks.jsonl` 保持不变。
- 258/9,322 条（2.77%）embedding 输入截断，索引记录具体 IDs；响应保留原文和截断标记。
- M1 准入基于用户批量确认并由代理代录的 300 条审核结论，不是本轮新增的独立全量正确性证明。
- `scripts/evaluate_retrieval.py` 新增逐请求日志，保留成功和失败 HTTP 响应，独占创建防止覆盖旧尝试。5 项新增测试验证 26/30 通过、25/30 失败、实验索引不可通过、冻结绑定被改不可通过、HTTP 失败保留已完成记录及拒绝覆盖。

## 未命中分析

Hybrid 未命中：01（美的收入）、02（格力收入）、04（比亚迪收入）、05（宁德时代收入）、08（五粮液收入）、09（泸州老窖收入）、28（五粮液现金流增长原因）。完整返回原文及 gold 见 [7 题失败证据](evidence/retrieval_formal/hybrid_failures.json)。

**标签覆盖范围。** 原始绑定器限定参考物理页，通用收入题却未在题面要求必须引用该页。已回查源 PDF 的两个例子：

| 题目 | 原 gold 页 | 实际 Top-5 返回的其他页证据 | 源文对照 |
|---|---:|---|---|
| 美的收入（01） | 7 | 第 96 页合并利润表：2025 年半年度营业收入 251,123,714 千元 | 与原问题要求的当前期间收入一致；同时区分公司口径的 537,660 千元 |
| 格力收入（02） | 7 | 第 22 页营业收入构成：本报告期营业收入合计 97,324,925,988.20 元 | 同页源文确认期间、项目和金额 |

两例支持“gold 未穷尽等价证据”的判断，**不代表其余未命中全部属于误判**。未据此修改原标签或增加正式分数。[源 PDF 摘录与哈希](evidence/retrieval_formal/equivalent_source_examples.json)。其他收入题需独立核对主体、合并/公司口径、期间、单位与科目，不能仅凭数字相同纳入 gold。

**真实检索缺口。** 题 28 的 gold 是第 7 页短注释：“注：主要系本报告期货款中现金收取比例较高，上年同期银行承兑汇票收取比例较高所致。”当前 chunk 有章节路径，但正文没有“经营活动现金流量净额”这个被解释对象。Top-5 返回了经营业绩介绍、母公司现金流量表、合并现金流量表和第 12 页财务变动表，未返回解释原因的注释。第 12 页变动表虽标有“注2”，现有展开只处理 KV 与同分片 Markdown，不连接正文脚注。短注释缺少被解释对象、表与注释未关联，是需要修复的上下文问题；仅凭本次 Top-5 不能进一步确定 gold 在候选召回还是重排环节被淘汰。

**结果多样性与口径。** 同一张表的 Markdown 与 KV 会同时占用主结果位置。部分查询还混入母公司口径证据，检索相关分数不等于口径正确。后续可在不修改事实原文的前提下研究同表视图去重、表注关联和明确的口径约束，并保留本次结果作为对照。

## 下一项待决策：相关性标签范围

建议在保留本次 v1 问题、gold 和失败分数的基础上，另建 v2 相关性标签：允许同一固定财报中经过源 PDF 核验、主体/科目/期间/口径/单位均一致的跨页等价证据。核验范围应覆盖全部问题和相关源页，不只挑本次返回的证据；先冻结 v2，再跑新的检索实验。不能用关键词包含或数值相同直接自动通过，也不能把 v2 分数覆盖成 v1 结果。

这是验收口径决策，依用户最初“需要我决策时停下”的要求，等待确认后再实施标签范围变更。M3 教师模型/API 尚未选择或调用。

## 复现与证据

```bash
# 已有 index/ 时拒绝覆盖；本次索引已成功生成，无须重复耗时构建。
.venv-retrieval/bin/python -m finmhqa.retrieval.build_index \
  --chunks data/candidates/docling/chunks.jsonl --out index \
  --qa-dir data/qa/docling_corpus --reviewed-samples data/qa/docling_corpus/reviewed.jsonl
.venv-retrieval/bin/python -m uvicorn finmhqa.retrieval.server:create_app \
  --factory --host 127.0.0.1 --port 8100
# 另一终端运行；使用新输出名以保留本次失败结果。
.venv-retrieval/bin/python scripts/evaluate_retrieval.py \
  --queries data/eval/retrieval_dev30.bound.jsonl --out data/eval/retrieval_dev30.recheck.json
```

正式服务在最终健康检查通过后已正常关闭，避免持续占用模型内存；可按上述命令重启，无须重建索引。

本地完整报告：`data/eval/retrieval_dev30.results.json`；逐请求原始响应：`data/eval/retrieval_dev30.results.requests.jsonl`。构建、服务、评测日志分别为 `data/retrieval_formal_build.log`、`data/retrieval_formal_server.log`、`data/retrieval_formal_eval.log`。

提交的小型证据：[结果摘要](evidence/retrieval_formal/summary.json)、[索引清单](evidence/retrieval_formal/index_manifest.json)、[冻结绑定](evidence/retrieval_formal/frozen_bound_queries.jsonl)、[验证与原件哈希](evidence/retrieval_formal/validation.json)。历史 18 条实验的 100% 命中仅为联调结果，不能替代这里的全量验收。
