# DeepSeek 作为 Judge 与教师候选

2026-09-23。模型为 `deepseek-v4.1-flash`，经 OpenCode Zen 的 OpenAI 兼容接口（`https://opencode.ai/zen/v1`）调用。适配器见 [openai_compat.py](../../src/finmhqa/agent/openai_compat.py)：只提供 JSON 模式，不提供任何工具（模型返回工具调用即拒绝）；每次调用保存原始请求和响应，并确定性转换为审计使用的事件格式，审计时从原始响应重算比对。回复不是合法 JSON 时最多重试 1 次；网络层错误（断连、429、5xx）最多重试 2 次；所有重试均有记录。key 只从 `.env` 或环境变量读取，已用 key 原值扫描全部输出，未发现泄露。

## 事先登记的门槛

运行前由用户确认，写入 [teacher_v2_deepseek.json](../../configs/teacher_v2_deepseek.json)：dev20 ≥17/20、轨迹审计 20/20、顶层错误 ≤1、平均工具 ≤6；Judge 在有人工参考后，误放行率不高于 Sol。

## 教师：dev20 实测

配置与 teacher_v1 完全相同，只换模型；Verifier 同样是 DeepSeek。

| 指标 | Sol rc1（无计算预留） | DeepSeek run1 |
| --- | ---: | ---: |
| 数值/实体正确 | 19/20 | **20/20** |
| 轨迹审计 | 20/20 | 20/20 |
| 顶层错误 | 0 | 0 |
| 工具 / 平均 | 101 / 5.05 | 84 / 4.20 |
| 模型调用 | 125 | 104 |
| 平均 / P95 耗时 | 186 / 293 秒 | 132 / 201 秒 |
| 输入 / 输出 token | 3.53M / 61K | 1.34M / 130K |
| 最终引用命中各跳锚点 | 43/48 | 44/48 |
| 最终引用块数 | 69 | 97 |
| JSON重试 / 网络错误 | — | 0 / 0 |

所有门槛均满足，按事先约定**采用 DeepSeek 作为教师**。证据：[评分](../evidence/teacher_v2/dev20_run1.evaluation.json)、[审计](../evidence/teacher_v2/dev20_run1.audit.json)、[manifest](../evidence/teacher_v2/dev20_run1_manifest.json)。

限制：只跑了一次。dev20 已经饱和（flat 策略此前也是 20/20），区分不出强弱。Sol 对照是 rc1，没有「最后一次工具留给计算」的规则，所以 006 的差异不能全部归因于模型。引用块更多的情况下，引用精确度未检查。

## Judge：18条冻结样本

17/18 完成，审计 17/17 通过。1 条因服务端断开连接失败，按「每条只调用一次」的约定原样保留；网络重试是在这次运行之后才加的。

- 与 Sol 判断一致的维度为 78/92（85%）。
- 已回答样本中，DeepSeek 判为六维全部有依据的是 11/14，Sol 是 6/15。DeepSeek 明显更宽松。
- 6 条受控编辑中，DeepSeek 在对应维度识别出 3 条（口径错误、数值无依据、缺表头引用）；漏掉 3 条：
  - 期间写错：它认为「实质正确」。
  - 引用了从未出现的 ID：理由里提到了，标签仍给 supported。
  - 求和前先舍入：Sol 也漏了。
- Sol 识别出 4 条，对未见 ID 那条判为 uncertain。

受控编辑的类型只是构造意图，不是人工真值，因此这里没有准确率。在人工参考完成前，**不采用 DeepSeek 作为唯一的 Judge 或质量门**。引用了从未出现过的 ID 这类错误可以由程序判定，应当放在规则层检查，不交给 Judge。证据见 [evidence/quality_judge_deepseek_v1](../evidence/quality_judge_deepseek_v1)。

用量：input_tokens=337,665，output_tokens=88,144（其中 reasoning 73,976）。
