# M3.1 学习说明：证据上下文与核验预算

本阶段在现有四角色图中增加一个工具动作，不增加图节点。预先固定的实验边界见[方案](../archive/AGENT_M31_PLAN.md)，原版结果仍保存在[M3报告](../archive/AGENT_M3_RESULTS.md)。

## 为什么命中数字还不够

检索命中“营业收入 100”，只能说明拿到了一个候选数字。能否使用，还取决于它属于哪家公司、哪一期、哪个报表口径，以及单位是什么。表头在上一页、单位在财务报告通用说明时，继续改写 query 不一定是最短路径。

索引里的 chunk 已含 source_sha256、company、report_period、page_start/page_end、parent_table_id。这些字段可以把一个已观察的数字连接回相关上下文，但不能代替正文事实。例如 report_period=2025H1 说明它来自半年报，不能证明某个表格列是本期而非上年同期。

## 一次上下文读取怎样执行

1. [角色契约](../../src/finmhqa/agent/graph_contracts.py)在原 PLAN_SCHEMA 的副本上增加 read_evidence_context；旧动作 schema 保留。模型必须提供 anchor_id、scope、cursor，未用分支为 null。
2. [图的 executor](../../src/finmhqa/agent/graph.py)先检查锚点已在本题 evidence 中，并检查共享工具预算。请求和响应写入真实轨迹；未知锚点、重复请求和跨来源响应成为可恢复错误。
3. [EvidenceContext](../../src/finmhqa/agent/evidence_context.py)在启动时读取已准入的 chunk_meta.jsonl，核对清单状态、SHA256、数量和 ID 唯一性，然后使用内存快照。这个工具不执行文件名参数，也不自行联网或重新解析 PDF。
4. 选择同一公司、源文件哈希和报告期间内的候选。table 返回同一父表的 Markdown 分段；page 返回锚点起始物理页相交的正文/原表；previous_page/next_page 返回前后物理页；document_units 返回包含单位和币种字样的正文候选。
5. 按页码、分段、ID 确定性排序。一次最多六条正文和 24,000 个字符；整条返回，不切掉表头或尾部。未返回完时给出 next_cursor，后续读取仍需消耗工具预算。
6. 返回的原始 chunk_id 进入 evidence，最终答案可以同时引用数字所在表和适用的单位说明。来源一致只证明来自同一文档；相邻页、通用单位的适用性仍需 Verifier 判断。

注意：page 返回“该页可用的准入 chunks”，不是完整 PDF 页。跨页 chunk 可能带上另一页的内容，原页码范围仍保留。document_units 采用宽匹配，可能包含局部表格正文，不保证第一个结果就是报告总声明。预先固定的[真实语料探针](../evidence/agent_m31_v1/context_probes.json)记录了分页次数；这项成本限制不能隐藏。

## 怎样预留最后一次核验

原图让 Planner 在第十次调用仍执行搜索，虽然工具找到了证据，却再无机会提交候选和验证。reserve_verifier=true 时，剩余两次调用会把 Planner 的 action enum 缩到 finish，并在模型输入中明确 finalize_only=true。Planner 可以提交已有证据支持的答案，也可以声明不足。最后一次调用由 Verifier 使用。

这不是预留一个“必定成功”的位置：计算没做、单位没补齐、最后核验不通过，仍然拒答。预算上限保持十次，且只用于 verified 发布模式；通过核验后原样发布，避免额外润色调用。测试使用 max_models=4 的短轨迹验证搜索→计算→候选→核验恰好到界时可发布，也验证最终核验发现冲突时不会发布。

## 数值精度与证据质量的区别

Decimal 计算器保证在给定操作数和单位下精确换算并按指定位置舍入；它不能证明操作数读对了。新提示要求优先采用原表精确金额，最后舍入，已有摘要数不能被描述成原始精度。当前没有逐操作数的强制 source cell 绑定，因此仍要审查实际计算输入、原文和最终解释。

同样，最终答案等于计算结果、引用 ID 曾被看到，只是确定性门禁。单位是否适用、比较对象是否一致、引用是否覆盖解释，属于语义核查；同一个 Sol Verifier 不等于独立 Judge。后续 M4 仍需单独建立质量校准和训练数据准入。

## 如何复现本轮运行

先按[M2正式结果](../archive/M2_FORMAL_RESULTS.md)启动本地检索服务。索引无需重建。

```bash
.venv/bin/python scripts/run_agent_dev.py \
  --questions docs/evidence/agent_dev20_v1/questions.jsonl \
  --out data/agent_m31_recheck/dev20 \
  --arms structured --workers 4 \
  --context-index index --reserve-verifier

.venv/bin/python scripts/run_agent_recovery.py \
  --questions docs/evidence/agent_dev20_v1/questions.jsonl \
  --out data/agent_m31_recheck/diagnostics \
  --context-index index --reserve-verifier \
  --scenarios unit_mask_first period_mask_first header_mask_first conflict_first
```

两个命令顺序运行，避免各自的客户端锁无法协调并行访问单容量检索服务。输出目录必须不存在，不能覆盖失败。运行入口只读题目文件，不读 gold；评分脚本离线读冻结 gold。配置开关和执行代码哈希写入 manifest。审计额外重放上下文读取，检查真实语料原文是否和轨迹一致。

代码/契约测试在主 .venv，真实检索依赖在 .venv-retrieval。测试通过、HTTP集成通过、模型答案正确、独立质量审核通过是不同层次，分别记录。
