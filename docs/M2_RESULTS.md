# M2 本地检索实现与联调结果

更新：2026-09-13。验证代码：`e0c5ba79fbb0428a789d2ba953bf819a55a30700`。

已完成授权模型下载、离线推理、FAISS/BM25/RRF/reranker、父表展开及 FastAPI 接口。真实模型与 HTTP 联调通过；**M2 正式验收尚未完成**。M1 的 300 条样本仍为 `PENDING_REVIEW`，正式 `index/` 未创建。实验数据和索引均位于 `data/experiments/retrieval_smoke/`。

## 模型与实现

| 用途 | 模型 | 固定 revision |
|---|---|---|
| Dense embedding | BAAI/bge-m3 | `5617a9f61b028005a4858fdac845db406aefb181` |
| Reranker | BAAI/bge-reranker-v2-m3 | `953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e` |

两套权重约 4.3 GiB，保存在 `data/models/retrieval/`；文件大小、SHA256 与配置记录见 [模型来源](evidence/retrieval/model_provenance.json)。组合来源文件 SHA256 为 `7c01c53fe2cfa65e4654de55e5a3c915d44cee7c7e8de2bd1131a7ccc9d85d48`。运行使用本地文件、CPU float32，无云端推理 API 调用。

采用模型卡给出的原生 Transformers 路径封装薄适配器：bge-m3 取 CLS 后 L2 归一化，1024 维；reranker 输出 sigmoid 分数。[bge-m3 模型卡](https://huggingface.co/BAAI/bge-m3)、[reranker 模型卡](https://huggingface.co/BAAI/bge-reranker-v2-m3)。当前不实现 bge-m3 的 sparse/ColBERT 分支。

Dense 使用 FAISS IndexFlatIP；BM25 使用 jieba 固定分词词典、关闭 HMM，索引以 bm25s 原生文件保存。三种模式均经同一 reranker 排序；hybrid 用 RRF k=60。默认候选 30、主结果 5，按公司名或代码筛选；KV 仅展开同公司、同来源、同父表且同分片的 Markdown。附加父表不计入主结果 Top-5，其分数明确标为继承值。响应保留完整证据文本和质量标记。

默认模型输入上限 1024 tokens，可能截断长文档；索引与响应显式记录截断，源证据原文保留。不能将这项工程限制隐去后解释为完整长表语义检索。

## 实际验证

| 验证 | 结果 | 边界 |
|---|---|---|
| 两套模型独立推理 | 3×1024 向量、单位范数；相关句得分高于无关句 | 短句探针，非财务问答正确率 |
| 检索测试 | `.venv-retrieval` 中 6 项通过 | 含真实 FAISS/BM25、注入模型的检索逻辑和原生库隔离回归 |
| M1 回归 | `.venv` 中 22 项通过，检索模块 1 项跳过 | M1 环境不安装检索依赖 |
| 真实实验建索引 | 18 条、1024 维，1 条 embedding 输入截断 | 来自未验收候选集 |
| 真实 HTTP 检索 | dense / bm25 / hybrid 各 4/4 Top-5 命中，共 12 次 200 | `n_cand=8`，预选目标证据，`DIAGNOSTIC_ONLY` |
| HTTP 边界 | 无效模式、空问题返回 422；不存在公司返回空结果 | 实际请求验证 |
| 正式入口 | 明确报错 `M1 acceptance required: review is not PASSED` | 未绕过审核，未创建正式索引 |
| 服务退出 | Application shutdown complete | 实验服务已停止，释放模型内存 |

四条样例为固定开发问题 01、11、18、26，实验集包含这些问题的目标证据、每家公司一条随机正文干扰项，以及必要的配对父表。**这样的选择存在明显偏置，100% 命中不能推广到全量数据。** 完整 30 条问题尚未运行正式检索；验收要求在已审核的全量索引上，以默认候选 30 运行冻结开发集，hybrid Top-5 >85%。绑定率和接口联调均不替代此要求。

小型证据：[索引清单](evidence/retrieval/smoke_index_manifest.json)、[样例选择](evidence/retrieval/smoke_selection.json)、[HTTP 摘要](evidence/retrieval/smoke_http_summary.json)、[验证与哈希](evidence/retrieval/validation.json)。完整原文响应留在本地 `data/qa/retrieval/smoke_http.json`。

## 真实失败与修复

同进程加载 Torch 与 FAISS 时，先后出现退出码 139（SIGSEGV）和 134（重复 OpenMP 初始化）。调整导入顺序只使建索引暂时通过，搜索仍失败。检查二进制依赖发现各自携带不同路径的 libomp；上游亦有 [macOS ARM 相同冲突报告](https://github.com/pytorch/pytorch/issues/149201)。

最终将 Torch 模型加载、embedding 和 rerank 放入持久本地子进程，经 stdin/stdout JSON 协议调用；FAISS/BM25 留在服务进程。父进程不导入 Torch，子进程不导入 FAISS，并设请求超时与退出清理。未使用 `KMP_DUPLICATE_LIB_OK`，未修改依赖二进制。修复后真实建索引、三模式查询和服务关闭通过。失败日志与中间实验索引仍保留在本地，未伪装成成功结果。

## 后续门禁

先完成 M1 固定 300 条样本的源 PDF 人工复核；修复后若候选变化，重新审计、抽样审核并绑定证据。通过后才能生成正式全量索引并执行 30 题检索验收。M3 教师 API、学生模型与训练资源尚未选择或调用，进入时另行决策。
