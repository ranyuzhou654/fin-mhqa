# 检索相关性标签 v2：源页等价证据

用户已确认允许将经源 PDF 核验的跨页等价证据纳入新版标签，并保留 v1 原始分数。本次只修订标签范围，不改问题、数据、索引、模型、检索参数或 >85% 的验收阈值。

## 冻结前的核对

候选发现脚本只读取 30 道原始问题、9,322 条候选证据和 10 份原始 PDF，不读取检索响应或排名。全量扫描 1,750 页；锚点搜索涉及 57 个不同源页，并额外检查切分后证据的实际页码。PDF 空白和千分位仅在候选匹配中规范化，正负数、期间、单位及列关系仍须独立审核。金额单位换算仅用于扩大候选发现范围，不会直接产生正确标签。

共审核 155 个“题目—候选证据”关联：保留 v1 的 56 个关联，接纳 66 个，拒绝 33 个。新增接纳证据来自 29 个物理页，已查看其 PDF 渲染图并核对表头、项目行、单位及期间；对应图片哈希写入审核记录。Markdown 与 KV 只在同一来源、同一父表和分片、且候选本身包含目标内容时分别记录。

v2 最终为 122 个题目—gold 关联，涉及 114 个不同 chunk。14 道题增加证据：10 道收入题及 4 道原因解释题；16 道结构化题保留原标签。每一道题均纳入扫描，不只处理 v1 失败题。解释题 28 增加第 12 页“注2”正文，只有现金流数值和“注2”标号的表格仍不算解释证据。

## 接纳与拒绝规则

- 新增收入证据必须属于同一公司、同一固定财报快照、当前半年期间的集团/合并收入或明确收入合计，且 chunk 暴露原题所需的金额单位。不得用营业总收入、母公司收入、行业数值或任意相同数字替代。
- 本版保守拒绝新增视图中缺少单位、期间/收入成本表头等必要语义的情况，即使原 PDF 的金额可对应，也不自动将不完整 chunk 计入 gold。
- 利润表题仍受报表类型、合并/公司口径、重述期间和括号列示等题意约束。美的研发费用明细、比亚迪研发支出或管理层变动分析等，不能仅因含相同数字就成为这些题的 gold。
- 茅台第 101 页数字片段匹配属于母公司收入分解，不能回答国外经销商数量，明确拒绝。
- 原版 gold 作为历史基线保留，不借本轮改写旧审核。v2 是保守、经核验的等价证据集合，不声称穷尽语义改写、四舍五入换算或所有可推导答案。

审核人员是本任务代理，使用源 PDF 和证据原文进行核对。代理已见过 v1 失败分析，因此不是盲审、不是新的独立测试集；新增标签带来的分数变化也不是检索模型或算法提升。

## 文件与防篡改

- [逐项审核记录](../../configs/retrieval_equivalence_v2_review.json)：每个候选的接纳/拒绝理由、原 chunk 哈希，以及新增证据的源页和渲染图哈希。
- [候选发现清单](../evidence/retrieval_v2/discovery.json)、[冻结标签](../evidence/retrieval_v2/frozen_bound_queries.jsonl)、[绑定摘要](../evidence/retrieval_v2/binding_summary.json)。
- [标签版本注册表](../../configs/retrieval_label_versions.json) 固定 v2 标签、问题、数据和审核文件哈希。冻结绑定 SHA256：`9bc87fe4de0ab7063effc599a6d4b577cfaca0ebd4134eaf0c206c62f7250e6c`。

绑定器要求全部发现候选各有且仅有一个明确决策，保留 v1 gold，检查主体、来源、证据哈希、单位、源页锚点及已审核图像哈希。评测在开始和结束时核对标签快照，核对原问题语义及 v2 注册审核哈希，防止只更新 sidecar 就把修改过的问题伪装成冻结开发集。

验证：审核应用测试 7 项通过；评测测试 8 项通过，含原 5 项和新增的语义改写、已注册 v2、审核文件被改等边界。测试验证门禁行为，不代替源页审核。

## 复现

```bash
# 输出目录/文件必须不存在，旧尝试不会被覆盖。
.venv/bin/python scripts/discover_retrieval_equivalence.py
# 源页渲染核对后填写独立审核配置；本次已完成并提交。
.venv/bin/python scripts/bind_retrieval_equivalence.py
.venv-retrieval/bin/python -m pytest tests/test_equivalence.py tests/test_retrieval_evaluation.py -q
# 启动已有正式索引，无须重建向量。
.venv-retrieval/bin/python -m uvicorn finmhqa.retrieval.server:create_app --factory --host 127.0.0.1 --port 8100
# 在另一个终端运行，使用独立 v2 输出路径。
.venv-retrieval/bin/python scripts/evaluate_retrieval.py \
  --queries data/eval/retrieval_dev30.v2.bound.jsonl --out data/eval/retrieval_dev30.v2.results.json
```

v1 标签和正式失败报告保持原样，见 [v1 全量结果](M2_FORMAL_RESULTS.md)。本文件及上述标签先于 v2 新一轮 HTTP 评测提交；实际结果另行记录。
