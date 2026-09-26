# 三模型 Judge 对比（无人工校准）

2026-09-23。用户选择方案 B：不做人工校准，只比较三个模型 Judge 在冻结校准包 v1 的 18 条样本上的判断。**这里没有真值，因此不计算准确率、误放行率或误拒绝率。**所有结论只描述各模型的判断倾向和相互分歧。

## 三个 Judge

| Judge | 调用方式 | 完成 | 独立性 |
| --- | --- | ---: | --- |
| Sol（gpt-5.6-sol） | Codex，每条一个新会话 | 18/18 | 与教师 teacher_v1 是同一模型 |
| DeepSeek（deepseek-v4.1-flash） | OpenCode Zen API，每条一次调用 | 17/18（1 条网络断开） | 与现任教师 teacher_v2 是同一模型 |
| Claude（Opus 5.5） | 6 个全新子代理上下文，每个 3 条，同组样本分到不同批次 | 18/18 | 见下方限制 |

三者的输入完全相同：评分标准、问题、最终输出、工具观测，以及各自的输出 schema。输入中都不含变换类型、来源组或其他 Judge 的意见。输出都通过同一校验：schema、证据 ID 必须出现在观测中、与冻结样本内容的哈希绑定。

Claude 的限制：
- 子代理没有可审计的原始 API 事件，只能做上述输出校验。
- 每个上下文审 3 条，不是每条一个上下文。
- 3 个批次在自检时看到过其他输出的文件名；其中批次 5 还打印出了其他条目的标签，这 3 条已由全新上下文重审，正式结果使用重审版本。
- 重审与原版在 3 条中有 4 个维度标签不同，说明单次 Judge 本身存在随机波动。
- 受控编辑和评分标准由此前的代理会话构造，Claude 与构造者的判断倾向可能相近。因此 Claude 在受控编辑上的「命中」，可能部分反映的是与构造意图一致，而不是更接近真实。

证据：[对比结果](../evidence/judge_comparison_v1/comparison.json)、[Claude 判断与隔离记录](../evidence/quality_judge_claude_v1)。复现命令：`PYTHONPATH=scripts .venv/bin/python scripts/compare_judges.py --judge sol ... --judge deepseek ... --judge claude ... --out <新路径>`。

## 结果（三方都完成的 17 条）

| | Sol | DeepSeek | Claude |
| --- | ---: | ---: | ---: |
| 已回答样本六维全部 supported | 6 | 11 | 5 |
| 已回答样本至少一维 missing/contradicted | 7 | 4 | 8 |
| uncertain 标签数 | 7 | 1 | 14 |
| 受控编辑目标维度判为 missing/contradicted | 4/6 | 3/6 | 6/6 |

两两一致率：Sol~DeepSeek 85%，Sol~Claude 80%，DeepSeek~Claude 77%（均为 92 个可比维度）。三方一致的有 67 个维度，存在分歧的 25 个。分歧集中在：单位适用性 8 个、期间 8 个、引用覆盖 7 个。这三项正是表头被遮蔽、单位要跨表推断的情形。

受控编辑的逐项结果：

| 编辑类型（构造意图） | Sol | DeepSeek | Claude |
| --- | --- | --- | --- |
| 数值无依据 | contradicted | contradicted | contradicted |
| 缺表头引用 | missing | missing | missing |
| 口径改错 | contradicted | contradicted | contradicted |
| 期间写错 | contradicted | supported | contradicted |
| 引用未出现的 ID | uncertain | supported | contradicted |
| 求和前先舍入 | supported | supported | contradicted |

规则检查：只有「引用未出现的 ID」这一条能由程序直接判定（引用了 `unobserved-control-evidence`）。

## 解读与建议

- DeepSeek 最宽松：放过了期间写错和未见引用。它适合做教师（生成），不适合单独把关。
- Claude 最严格，但 uncertain 最多；在真实记录上也频繁给出 uncertain，没有人工参考就无法区分「严格」和「误拒」。
- 三个模型都在表头遮蔽、单位跨表推断上分歧最大，这是题目本身的难点，不是某一个 Judge 的问题。

建议的 M4 质量门（未经人工校准，按此注明）：

1. 规则层先行：引用必须出现在观测中、最终数值必须等于计算工具的结果、计算可重放。能用程序判定的错误不交给 Judge。
2. 由非教师模型担任 Judge。任一 Judge 在任一维度判 missing/contradicted 即拒绝；判 uncertain 的样本不进入训练集，另行记录。
3. 在这 18 条样本上，任意两个 Judge 取并集都达不到三者并集的 6/6。但这是按构造意图统计的，不能当作检出率。
