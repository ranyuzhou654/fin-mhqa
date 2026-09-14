# 质量校准 v1：审核标准、样本与离线评分

2026-09-14。本阶段已完成质量校准基础设施和冻结审核包，**尚未完成质量校准**：18条人工参考标签均为空，没有执行Judge，也没有质量得分或训练准入。没有新模型/API/算力调用。先前源数据审核通过不自动覆盖这里的答案和轨迹审核。

## 从哪里审核

打开[审核入口](evidence/quality_calibration_v1/REVIEW.md)。每条有最终输出、可读证据页以及完整时序JSON。先复制[空白人工模板](evidence/quality_calibration_v1/reference.template.json)为本地 `data/qa/quality_calibration_v1/reviewed.json`，再填写，不修改冻结模板。每条ID绑定内容SHA256，改动问题、最终输出或工具观测后，旧标签不能复用。

当前[初始状态](evidence/quality_calibration_v1/initial_status.json)为 PENDING_HUMAN_REFERENCE，0/18已审核，metrics=null。没有代替用户填入通过标签，也没有把此前本代理的文本核查当作人工真值。审核人字段是归属声明，不是身份认证或电子签名。

## 固定样本如何选择

共18条，12个来源组，均只用于Dev校准：

- 8条M3.1真实开发运行：002、005、006、007、008、011、018、019，涵盖数值正确但引用有缺口、跨页补证、精度修复与3条拒答。
- 4条M3.1真实诊断运行：单位遮蔽、期间遮蔽、表头遮蔽和显式冲突。真实运行中包含已声明的响应变换，并非自然分布。
- 6条受控编辑：改错数值、移除表头引用、改错期间、改错口径、插入未见引用、在求和前舍入。最后一项连同计算参数/结果一并构造，明确属于人工构造的对照，不是真实模型轨迹。其最终两位小数仍可正确，用于检验精度判断。

这些变换是设计意图，不是已审核标签。参考审核可以判定不确定或指出变换并未产生预想问题；不能按变换名称自动填错。样本特意挑选难例且含配对编辑，不能估计总体错误率；同来源的真实记录与编辑必须保持同组，不能拆到Train/Test两侧。没有新增正式测试集。

## 七个维度怎样判

| 字段 | 检查内容 |
| --- | --- |
| numeric_grounding | 数值是否有原文与计算支持，输出是否对应实际运算 |
| unit_applicability | 单位是否适用于目标表；别的局部表格单位不能直接套用 |
| period | 金额对应本期还是上期，不能仅靠半年报年份推断 |
| scope | 合并/母公司、归母/全部净利润是否一致 |
| citation_coverage | 最终引用是否覆盖关键前提；已看到但未引用不等于引用完整 |
| precision | 是否使用原始精确金额，在最终输出时才按要求舍入 |
| abstention | 仅针对拒答：当前实际可见证据是否确实不足 |

每维标签使用 supported（支持）、missing（缺少依据）、contradicted（冲突）、uncertain（待裁定）、not_applicable（不适用）。每项标签都要理由；证据ID只能填实际观测中出现的ID。对于未知引用，在理由说明错误ID，不要把它填入真实证据ID列表。

回答样本前六维必须判定，abstention填not_applicable并注明已回答。拒答样本前六维为not_applicable，abstention必须判断：当前观测确实不足为supported，观测已足够却拒答为contradicted，无法裁定为uncertain。合理拒答不会被计为六维全通过的回答样本。

判断基于工具实际观测，不基于整份语料可能包含什么。可读证据页把最终引用的最近一次观测放在前面；完整JSON保留较早遮蔽版本与读取顺序，适合检查模型是否在证据补齐前提前推断。同一父表的明确表头可用于关联，但邻页相邻本身不能证明同表。

## Judge 输入与人工标签怎样隔离

[judge_inputs](evidence/quality_calibration_v1/judge_inputs)下每条文件只包含统一rubric及question/final/observations。没有原Planner理由、Verifier判决、gold答案、人工标签、受控变换名称或来源组名。所有病例使用稳定的不含题号的ID，映射单独留在manifest供审计。

这是输入隔离，不是独立测试或模型独立性的证明。若Judge仍使用内置Sol，即便单独上下文，也仍有同模型偏差；更换厂商也不能替代人工参考校准。Judge模型及调用方式需要用户确定，本阶段只准备输入，不自动派发请求。

Judge的结果格式与[judge.template.json](evidence/quality_calibration_v1/judge.template.json)一致，reviewer_type为model，记录真实模型名/版本与执行时间。后续执行器还应保留原始输出、提示、模型配置和用量；当前没有声称这些运行记录已经存在。

## 评分如何使用

```bash
.venv/bin/python scripts/score_quality_calibration.py \
  --packet docs/evidence/quality_calibration_v1 \
  --reference data/qa/quality_calibration_v1/reviewed.json \
  --predictions data/qa/quality_calibration_v1/judge.json \
  --out data/qa/quality_calibration_v1/comparison.json
```

输出必须用新路径。评分器先核对所有冻结输入哈希、ID集合、逐条内容哈希、维度集合和证据引用；参考标签必须声明human且具有审核人、时间和理由。缺参考标签时输出PENDING_HUMAN_REFERENCE；参考齐全但Judge未完成则PENDING_JUDGE，都不生成质量分数。

双方完成后逐维报告准确一致率、混淆计数、误放行和误拒绝，以及分歧清单。**误放行率的分母是人工判定missing/contradicted的样本数**，不是全部18条；没有这样的样本时返回null。人工uncertain和not_applicable不作为已裁定真值进入一致率分母，但保留数量；Judge回答uncertain也单独可见，不能把它算成成功识别错误。没有整体Faithfulness分数，没有自动训练放行阈值，输出release_approved=false。

## 代码学习与验证

[构建脚本](../scripts/build_quality_calibration.py)从已归档轨迹提取工具时序与最终输出，构造配对编辑，生成模型输入/人类可读视图/空标签模板，最后冻结输入哈希。[校准模块](../src/finmhqa/qa/calibration.py)处理内容绑定、标签约束和对照统计；[评分入口](../scripts/score_quality_calibration.py)校验冻结文件并调用统计，不调用模型。

[测试](../tests/test_quality_calibration.py)覆盖内容变动使标签失效、代理建议不能冒充人工参考、漏题/未见引用拒绝、拒答不能算作完整回答、uncertain分母以及真实18条包不携带Verifier判决。全仓库112项通过、1项跳过，见[测试记录](evidence/quality_calibration_v1/tests.xml)。此前跳过的检索依赖项本阶段仍未冒充通过；本阶段没有重新执行检索服务或模型实跑。

下一决策是Judge模型与执行方式。人工参考完成后才能判断它的误放行情况，之后再决定如何修订合成过滤规则与扩大数据规模。
