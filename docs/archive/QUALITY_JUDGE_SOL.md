# Sol 独立上下文质量复核

用户已明确授权继续使用内置Sol作为Judge。使用已冻结质量校准v1的18条输入，每条一次新的ephemeral会话，reasoning=low，最多4条并发；不复用教师会话，不发送人工标签、来源组、变换名称、gold或原Verifier判决。使用已有Codex登录，不新增外部API采购。

每条最多一次调用，异常原样保留；不自动反复生成至标签符合预期。结构化输出仅包含七维标签、逐维理由、真实证据ID和备注；输出schema按回答/拒答限制not_applicable，证据ID限定于该条已观察记录。病例ID、内容哈希、模型归属与时间由执行器绑定。

审计将核对真实原生事件、模型原文、输入与schema、用量、输出病例绑定和会话ID唯一性。协议或证据ID校验通过只证明输出符合约束，不代表语义结论正确。保留同模型偏差：新上下文不能使Sol变成独立厂商模型。

人工参考仍为0/18，故本轮输出Judge标签分布及待人工核查清单，不计算准确率、误放行率或整体Faithfulness，不授予训练准入。实际结果见下节。

代码入口：[执行器](../../scripts/run_quality_judge.py)、[输出契约](../../src/finmhqa/qa/judge.py)。

## 实际结果

18/18条均完成，各执行一次，零顶层运行失败；18个不同的原生会话ID，经审计确认输入与冻结病例及rubric一致，输出与真实模型事件一致，18/18审计通过。见[审计](../evidence/quality_judge_sol_v1/run/audit.json)、[模型原始判定](../evidence/quality_judge_sol_v1/run/predictions.json)、[逐条可读判定](../evidence/quality_judge_sol_v1/run/REVIEW.md)。没有进行重试挑选。

| 样本类别 | Judge意见 |
| --- | --- |
| 15条已回答病例 | 6条六维全部supported，9条至少一维有缺失、冲突或不确定 |
| 3条拒答病例 | 2条abstention=supported，1条contradicted |

这是Judge标签分布，不是质量通过率或已校准的准确率。七维分布、逐条标签及来源组保存在[结果摘要](../evidence/quality_judge_sol_v1/run/summary.json)。原始问答的数值评分不被本轮模型意见覆盖。

六条受控编辑中，数值、期间、口径改错及删除表头引用这四项，其对应目标维度被判为contradicted或missing；未见引用的对应维度为uncertain，提前舍入的precision为supported。变换意图不是人工真值，不能计算成“检出率4/6”或“误放行1/6”。

## 优先人工裁定项

1. **提前舍入（qc-19b9a14eb7c3）**：Judge理由承认先舍入再求和，但因最终数值未变而判precision=supported。它与已冻结“是否使用原始精度并最后舍入”的规则存在不一致，需要裁定规则执行。不能修改原输出使它看起来检出了问题。
2. **未见引用（qc-64533eeb4921）**：程序可验证新增ID不在任何观测中；Judge也承认不可核验，但把引用覆盖判为uncertain。需要确定无效ID如何落入missing/contradicted，并与跨页证据自身的歧义区分。
3. **配对稳定性（qc-71f13982ee6c / qc-64533eeb4921）**：已验证两条问题、观测、解释完全相同，只多一个无效引用；单位和期间判定却由supported变为uncertain。这是需要核查的配对差异，不是重复采样稳定性指标。
4. **拒答语义（qc-2263ee587605）**：Judge按当前可见证据充分性判为不必要拒答；原Agent实际因修改加再核验的预算不足停止。前者不能被理解为系统应绕过核验预算直接发布。后续可分别评价证据充分性与运行终止策略。

这些是本代理依据真实输出整理的核查建议，不是新增人工标签。[机器可读核查项](../evidence/quality_judge_sol_v1/priority_review.json)。如要尽量降低锚定效应，先在[无Judge标签的审核入口](../evidence/quality_calibration_v1/REVIEW.md)独立填写参考，再查看模型意见与分歧。

## 验证和当前边界

全仓库120项测试通过、1项跳过，见[测试记录](../evidence/quality_judge_sol_v1/tests.xml)。新增测试覆盖模型证据ID限制、回答/拒答标签隔离、非空理由、原始输入/输出篡改和越权原生工具事件拒绝。实际运行无越权工具事件；3条原生error通知说明技能描述为适配上下文预算而缩短，随后正常完成，完整通知保留在审计中。故零顶层失败不等于所有原生事件都没有通知。

本轮usage合计input_tokens=607,760，output_tokens=17,992，reasoning_output_tokens=3,959；reasoning子字段不与output再次叠加。模型仍通过既有Codex登录额度调用，未采购外部API。

人工参考仍为0/18，[校准状态](../evidence/quality_judge_sol_v1/calibration_status.json)保持PENDING_HUMAN_REFERENCE、metrics=null。复核本身已完成，校准尚未完成；没有授予Train/Test发布或SFT/GRPO准入。下一步需要真实人工参考，之后使用已有离线评分器计算逐维一致率、误放行与误拒绝，不需要再次选择Judge模型。

复现命令（始终使用新的输出目录）：

```bash
.venv/bin/python scripts/run_quality_judge.py \
  --packet docs/evidence/quality_calibration_v1 \
  --out data/qa/quality_judge_sol_recheck --workers 4
.venv/bin/python scripts/audit_quality_judge.py \
  --packet docs/evidence/quality_calibration_v1 \
  --run data/qa/quality_judge_sol_recheck \
  --out data/qa/quality_judge_sol_recheck/audit.json
```

运行输入大文件留在本地data/，Git保存冻结输入包、模型原始事件、输出/schema/usage、执行代码快照和所有运行文件哈希。审核与评分说明见[质量校准文档](QUALITY_CALIBRATION.md)。
