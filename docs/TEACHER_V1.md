# 教师配置 teacher_v1：M3.1 失败修复与冻结

2026-09-23。目标是停止在 dev20 上迭代，固定一个供 M4 合成（可达性验证、轨迹采集）使用的教师配置。配置文件为 [configs/teacher_v1.json](../configs/teacher_v1.json)，运行器使用 `--config` 读取，manifest 同时记录配置 SHA256。修改任何字段都须新建配置版本。

## 三个失败的根因与修复

| 题 | M3.1 实际根因（逐条查看轨迹确认） | 修复 |
| --- | --- | --- |
| 006 | 茅台合并现金流量表跨页，p32 续表的 Docling 表头为“单位：未标注”，parent_table_id 不同，`scope=table` 读不到目标行；模型也没有尝试 next_page | 新增 `table_continuation`：只返回下一页、同章节、表头未标注的 table_md，标为未经证明的续表候选；`scope=table` 返回 `continuation_candidates` 计数 |
| 011 / 007 | `document_units` 按页码排序，比亚迪“财务报表及附注的单位为：千元”（p63）排第 13 位，011 只读了第一页候选 | 报告级明确声明（财务报告章节内、短文本）排前，再按与锚点的页距排序；仍只是候选，不自动适用 |
| 007 | 8 次工具各占 1 次 Planner 调用，加上 finish 与 verify 正好 10 次；Verifier 一旦拒绝就没有修订余量 | `max_models` 10→12，即工具 8 + 提交/核验 2 + 一次修订 2；提示词中的预算随参数显示 |
| 006（rc1） | rc1 已通过续表读到 13,119,061,031.33 元，但 8 次工具用尽，无法最终换算，最终拒答 | `reserve_calculation`：只剩 1 次工具时，Planner 只能 calculate 或 finish |

上下文读取器新增 `version` 参数；未记录版本的历史运行按 v1 重放。已验证归档 M3.1 dev20 在新代码下重放审计 20/20 通过。审计预算上限改为从 manifest 读取。

## 实测

| 运行 | 配置 | 正确 | 工具 / 检索 / 模型调用 | 审计 |
| --- | --- | ---: | --- | --- |
| M3.1 | 旧预算 10，无续表 | 17/20 | 101 / 57 / 120 | 20/20 |
| rc1 | teacher_v1，无 reserve_calculation | 19/20 | 101 / 53 / 125 | 20/20 |
| 最终冻结运行 | teacher_v1 完整版 | **未完成** | — | — |

rc1 中 007、011 已答对，006 按上表拒答；上下文读取中 `table_continuation` 7 次、`table` 3 次、`document_units` 3 次。证据见 [评分](evidence/teacher_v1/rc1_dev20.evaluation.json)、[审计](evidence/teacher_v1/rc1_dev20.audit.json)、[manifest](evidence/teacher_v1/rc1_manifest.json)。rc1 input_tokens=3,525,317，output_tokens=61,201。

最终冻结运行在第 4 题之后遇到 Codex 账户用量上限，17 题以 `turn.failed` 终止。该运行不计分，原样保留在本地 `data/teacher_v1/aborted_quota_dev20/`。

限制：以上仍是单次开发运行。006 的修复在同一 dev20 上诊断得出，存在对开发集过拟合的可能；全语料 2,511 个表中有 678 个存在续表候选，额外读取会增加工具成本。19/20 与此前 19/20 不构成统计差异。

## 复现（额度恢复后）

```bash
.venv-retrieval/bin/python -m uvicorn finmhqa.retrieval.server:create_app --factory --host 127.0.0.1 --port 8100
# 另一终端：
.venv/bin/python scripts/run_agent_dev.py --questions data/agent_dev20_v1/frozen/questions.jsonl \
  --out data/teacher_v1/dev20 --config configs/teacher_v1.json --workers 4
cd scripts && ../.venv/bin/python evaluate_agent_dev.py --run ../data/teacher_v1/dev20 \
  --gold ../data/agent_dev20_v1/gold.jsonl --freeze ../data/agent_dev20_v1/frozen/freeze.json \
  --out ../data/teacher_v1/dev20.evaluation.json && cd ..
PYTHONPATH=scripts .venv/bin/python scripts/audit_agent_run.py --run data/teacher_v1/dev20 --out data/teacher_v1/dev20.audit.json
```
