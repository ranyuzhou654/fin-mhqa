# 文档索引

**先读 [项目报告](REPORT.md)**，它用一页汇总了全部结论和证据链接。接下来要做的全部事项见[路线图](ROADMAP.md)。

## 实验（按时间顺序，每项都是先登记设计、再运行）

| 文档 | 内容 |
| --- | --- |
| [S6 基线](experiments/BENCHMARK_V1_S6_BASELINES.md) | 闭卷、固定 RAG、单模型 Agent、四角色 Agent，各 3 次，逐级做配对比较 |
| [格式修复](experiments/FORMAT_REPAIR_V1.md) | JSON schema 校验加一次修复；另附核验角色在干净证据上的决策分析 |
| [故障注入设计](experiments/FAULT_INJECTION_V1_PLAN.md) · [结果](experiments/FAULT_INJECTION_V1_RESULTS.md) | 三类盲注入的证据错误，比较单模型与四角色 |
| [核验角色 v2 设计](experiments/VERIFIER_V2_PLAN.md) · [结果](experiments/VERIFIER_V2_RESULTS.md) | 来源优先级、期间列核对、交叉核对、不重复拒绝 |
| [规划者 v2 设计](experiments/PLANNER_V2_PLAN.md) · [阶段结果](experiments/PLANNER_V2_RESULTS.md) | 规划上限修复；无故障 Dev 待补 |
| [上下文管理 v1](experiments/CONTEXT_V1_PLAN.md) | 离线测算各种压缩策略，选定无损压缩并登记评估方案；真实评估待额度恢复 |

## 评测集 benchmark_v1

| 文档 | 内容 |
| --- | --- |
| [设计](benchmark/BENCHMARK_DESIGN.md) | 题型、答案生成原则、Dev/Test 划分与防泄漏 |
| [事实台账](benchmark/FACT_LEDGER_V1.md) | 从报表中抽取事实，用会计恒等式和多来源交叉核验，最终收录 298 条事实 |
| [题目生成](benchmark/BENCHMARK_V1_GENERATION.md) | 6 类模板，答案由程序用 Decimal 计算 |
| [质量检查](benchmark/BENCHMARK_V1_QUALITY.md) · [Judge 对比](benchmark/JUDGE_COMPARISON_V1.md) | 改写题目、两个模型评判歧义、可达性检查、人工抽查 |
| [冻结与使用](benchmark/BENCHMARK_V1_FROZEN.md) | 冻结清单、Test 保护、评分规则 |

## 系统组成

| 文档 | 内容 |
| --- | --- |
| [数据来源](system/DATA_SOURCES.md) | 10 家公司 2025 年半年报的来源和哈希 |
| [语料](system/DOCLING_CORPUS_RESULTS.md) | Docling 解析得到 9,322 条证据块及其审核 |
| [检索](system/M2_V2_RESULTS.md) | 混合检索验收（v2 标签） |
| [Agent 设计 M3](system/AGENT_M3_DESIGN.md) · [M3.1](system/AGENT_M31_DESIGN.md) | 四角色状态、反馈契约、上下文工具与核验预算 |
| [模型选型](system/TEACHER_V2_DEEPSEEK.md) | 用 DeepSeek 替代 Sol 的依据和门槛 |
| [复现指南](system/REPRODUCE.md) | 从 PDF 到实验的全部命令 |
| [运行 harness](system/HARNESS.md) | 错误分类、额度用完即停、逐题续跑、未完成运行不评分、录制—重放回归测试 |

## 示例与项目管理

- [示例轨迹](examples/)：可以用 `scripts/demo.py --replay` 重放
- [实施进度](IMPLEMENTATION_STATUS.md)：按时间记录的全部阶段、失败与数值来源
- [范围调整与差距](PROJECT_GAP_AND_ACCEPTANCE.md)：2026-09-23 起不再做 SFT 和 RL，转为 Agent 项目
- [归档](archive/README.md)：早期阶段文档，保留作为历史证据
- [证据文件](evidence/)：各项实验的报告、审计与日志（JSON）
