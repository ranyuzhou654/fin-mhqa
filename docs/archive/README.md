# 归档

这里是已经被后续工作取代、或属于早期阶段的文档，原样保留作为历史证据。文中链接已随目录迁移更新，数字仍以原文为准。当前结论见[项目报告](../REPORT.md)。

| 阶段 | 文档 | 被什么取代 |
| --- | --- | --- |
| 原始方案 | [设计方案](original_design/01_金融多跳问答系统_设计方案.md)、[实现文档](original_design/02_金融多跳问答系统_实现文档.md) | 其中 SFT/GRPO 训练部分已移出范围，见[范围调整](../PROJECT_GAP_AND_ACCEPTANCE.md) |
| M1 解析 | [M1 结果](M1_RESULTS.md)、[解析器决策](M1_PARSER_DECISION.md)、[Docling 小样](DOCLING_PILOT_RESULTS.md) | [Docling 全量语料](../system/DOCLING_CORPUS_RESULTS.md) |
| M2 检索 | [M2 决策](M2_DECISION.md)、[M2 联调](M2_RESULTS.md)、[v1 全量验收](M2_FORMAL_RESULTS.md)、[v2 标签](RETRIEVAL_LABELS_V2.md) | [v2 验收结果](../system/M2_V2_RESULTS.md) |
| M3 Agent | [M3 决策](M3_DECISION.md)、[M3 结果](AGENT_M3_RESULTS.md)、[dev20 审核](AGENT_DEV20_REVIEW.md)、[dev20 轨迹](AGENT_DEV20_TRAJECTORIES.md)、[已核验轨迹](AGENT_VERIFIED_TRAJECTORIES.md)、[恢复实验](AGENT_RECOVERY_REVIEW.md) | [S6 基线](../experiments/BENCHMARK_V1_S6_BASELINES.md) 与 benchmark_v1 |
| M3.1 | [方案](AGENT_M31_PLAN.md)、[结果](AGENT_M31_RESULTS.md)、[诊断](AGENT_M31_DIAGNOSTICS.md)、[轨迹](AGENT_M31_TRAJECTORIES.md)、[teacher_v1](TEACHER_V1.md) | [DeepSeek 选型](../system/TEACHER_V2_DEEPSEEK.md) |
| 桥接与合成小样 | [桥接设计](BRIDGE_TEACHER_DESIGN.md)、[桥接结果](BRIDGE_TEACHER_RESULTS.md)、[桥接轨迹](BRIDGE_TEACHER_TRAJECTORIES.md)、[桥接审核](BRIDGE_PILOT_REVIEW.md)、[Sol 合成小样](SYNTH_PILOT_SOL_RESULTS.md)、[小样审核](SYNTH_PILOT_SOL_REVIEW.md) | benchmark_v1（程序计算答案） |
| 质量评判 | [质量校准](QUALITY_CALIBRATION.md)、[Sol 复核](QUALITY_JUDGE_SOL.md) | [三模型 Judge 对比](../benchmark/JUDGE_COMPARISON_V1.md) |

说明：部分已执行的代码（例如 `scripts/run_agent_dev.py`、`src/finmhqa/bench/faults.py`）在注释里仍写着旧的文档路径。这些文件的 sha256 记录在历史运行的清单中，为了不破坏审计中的「代码与运行一致」检查，这些注释没有修改。
