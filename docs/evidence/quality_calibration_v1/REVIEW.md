# 质量校准审核包

18条：12条真实记录、6条受控编辑。所有标签待审核，原先审核通过授权不覆盖本包。

先复制 reference.template.json 为 reviewed.json，再按对应ID填写 labels、rationale、evidence_ids、reviewer和reviewed_at；保留原模板不变，每项均需理由。证据ID仅填本条observations中真实返回的ID。

supported=支持；missing=缺证据；contradicted=与证据冲突；uncertain=无法裁定；not_applicable仅按rubric的回答/拒答规则使用。

numeric_grounding数值依据；unit_applicability单位适用；period期间；scope口径；citation_coverage引用覆盖；precision精度；abstention拒答依据。

## qc-19b9a14eb7c3

来源组：agent-dev-019；类型：controlled_edit_not_real_execution；变换：premature_rounding。

美的集团与贵州茅台的经营活动产生的现金流量净额合计是多少？均采用2025年上半年合并口径，金额以亿元表示，四舍五入保留两位小数。

最终输出：{"status": "answered", "answer_value": "504.00", "answer_unit": "亿元", "selected_company": null, "citations": ["541efa4968594bc1d06afe03", "a4ee97ba53023666d5a3eb91", "9ed667801a10ec1a0e66cc11"], "explanation": "美的集团金额先保留两位小数为372.81亿元，贵州茅台为131.19亿元；将上述舍入值相加得到504.00亿元。"}

[可读证据](review_cases/qc-19b9a14eb7c3.md) · [完整观测与审核标准](judge_inputs/qc-19b9a14eb7c3.json)

## qc-1eafc7a669d4

来源组：agent-dev-008；类型：controlled_edit_not_real_execution；变换：wrong_scope。

根据半年报，比较五粮液与亿纬锂能营业收入，选择收入较高的一家公司，再查询这家公司的经营活动产生的现金流量净额是多少？均采用2025年上半年合并口径，金额以亿元表示，四舍五入保留两位小数。

最终输出：{"status": "answered", "answer_value": "311.37", "answer_unit": "亿元", "selected_company": "五粮液", "citations": ["cad29d5ea24462e1a71bf255", "85fe6d961927ba26380060b3", "e65f385eaac83f299736117b", "fa37907a92ff3f3d7ef3124a"], "explanation": "2025年上半年母公司口径营业收入：五粮液52,770,984,383.52元，亿纬锂能28,169,564,043.41元；五粮液高246.01亿元，因此选择五粮液。其母公司现金流量表中经营活动产生的现金流量净额为31,136,736,628.58元，换算并四舍五入为311.37亿元。"}

[可读证据](review_cases/qc-1eafc7a669d4.md) · [完整观测与审核标准](judge_inputs/qc-1eafc7a669d4.json)

## qc-2263ee587605

来源组：agent-dev-007；类型：actual_record；变换：original。

根据半年报，比较比亚迪与海尔智家营业收入，选择收入较高的一家公司，再查询这家公司的归母净利润是多少？均采用2025年上半年合并口径，金额以亿元表示，四舍五入保留两位小数。

最终输出：{"status": "insufficient", "answer_value": "", "answer_unit": "", "selected_company": null, "citations": [], "explanation": "证据或运行预算不足：MODEL_BUDGET"}

[可读证据](review_cases/qc-2263ee587605.md) · [完整观测与审核标准](judge_inputs/qc-2263ee587605.json)

## qc-3bae5a01939f

来源组：conflict_first；类型：actual_record；变换：original。

根据半年报，比较美的集团与宁德时代营业收入，选择收入较高的一家公司，再查询这家公司的归母净利润是多少？均采用2025年上半年合并口径，金额以亿元表示，四舍五入保留两位小数。

最终输出：{"status": "answered", "answer_value": "260.14", "answer_unit": "亿元", "selected_company": "美的集团", "citations": ["407d90148388cf7072a341ab", "858fb75185f7e9db4dfc04ce", "4b28ba1999c0652b8e5498b3", "1c7a08659829b3d158703c20"], "explanation": "2025年上半年合并营业收入：美的集团251,123,714千元，宁德时代178,886,253千元；美的集团高出722.37亿元，因此选择美的集团。其归属于母公司股东的净利润为26,013,690千元，换算并四舍五入后为260.14亿元。"}

[可读证据](review_cases/qc-3bae5a01939f.md) · [完整观测与审核标准](judge_inputs/qc-3bae5a01939f.json)

## qc-45df6728c7dd

来源组：period_mask_first；类型：actual_record；变换：original。

根据半年报，比较美的集团与宁德时代营业收入，选择收入较高的一家公司，再查询这家公司的归母净利润是多少？均采用2025年上半年合并口径，金额以亿元表示，四舍五入保留两位小数。

最终输出：{"status": "answered", "answer_value": "260.14", "answer_unit": "亿元", "selected_company": "美的集团", "citations": ["312228f2c06f020b2d6cd9d4", "858fb75185f7e9db4dfc04ce", "4b28ba1999c0652b8e5498b3"], "explanation": "2025年上半年合并口径营业收入：美的集团251,123,714千元，宁德时代178,886,253千元；计算验证美的集团高722.37亿元，因此选择美的集团。其归属于母公司股东的净利润为26,013,690千元，换算并四舍五入至两位小数为260.14亿元。"}

[可读证据](review_cases/qc-45df6728c7dd.md) · [完整观测与审核标准](judge_inputs/qc-45df6728c7dd.json)

## qc-64533eeb4921

来源组：agent-dev-008；类型：controlled_edit_not_real_execution；变换：unseen_citation。

根据半年报，比较五粮液与亿纬锂能营业收入，选择收入较高的一家公司，再查询这家公司的经营活动产生的现金流量净额是多少？均采用2025年上半年合并口径，金额以亿元表示，四舍五入保留两位小数。

最终输出：{"status": "answered", "answer_value": "311.37", "answer_unit": "亿元", "selected_company": "五粮液", "citations": ["cad29d5ea24462e1a71bf255", "85fe6d961927ba26380060b3", "e65f385eaac83f299736117b", "fa37907a92ff3f3d7ef3124a", "unobserved-control-evidence"], "explanation": "2025年上半年合并口径营业收入：五粮液52,770,984,383.52元，亿纬锂能28,169,564,043.41元；五粮液高246.01亿元，因此选择五粮液。其合并现金流量表中经营活动产生的现金流量净额为31,136,736,628.58元，换算并四舍五入为311.37亿元。"}

[可读证据](review_cases/qc-64533eeb4921.md) · [完整观测与审核标准](judge_inputs/qc-64533eeb4921.json)

## qc-67bf73499d78

来源组：agent-dev-008；类型：controlled_edit_not_real_execution；变换：unsupported_number。

根据半年报，比较五粮液与亿纬锂能营业收入，选择收入较高的一家公司，再查询这家公司的经营活动产生的现金流量净额是多少？均采用2025年上半年合并口径，金额以亿元表示，四舍五入保留两位小数。

最终输出：{"status": "answered", "answer_value": "312.37", "answer_unit": "亿元", "selected_company": "五粮液", "citations": ["cad29d5ea24462e1a71bf255", "85fe6d961927ba26380060b3", "e65f385eaac83f299736117b", "fa37907a92ff3f3d7ef3124a"], "explanation": "2025年上半年合并口径营业收入：五粮液52,770,984,383.52元，亿纬锂能28,169,564,043.41元；五粮液高246.01亿元，因此选择五粮液。其合并现金流量表中经营活动产生的现金流量净额为31,136,736,628.58元，换算并四舍五入为312.37亿元。"}

[可读证据](review_cases/qc-67bf73499d78.md) · [完整观测与审核标准](judge_inputs/qc-67bf73499d78.json)

## qc-71f13982ee6c

来源组：agent-dev-008；类型：actual_record；变换：original。

根据半年报，比较五粮液与亿纬锂能营业收入，选择收入较高的一家公司，再查询这家公司的经营活动产生的现金流量净额是多少？均采用2025年上半年合并口径，金额以亿元表示，四舍五入保留两位小数。

最终输出：{"status": "answered", "answer_value": "311.37", "answer_unit": "亿元", "selected_company": "五粮液", "citations": ["cad29d5ea24462e1a71bf255", "85fe6d961927ba26380060b3", "e65f385eaac83f299736117b", "fa37907a92ff3f3d7ef3124a"], "explanation": "2025年上半年合并口径营业收入：五粮液52,770,984,383.52元，亿纬锂能28,169,564,043.41元；五粮液高246.01亿元，因此选择五粮液。其合并现金流量表中经营活动产生的现金流量净额为31,136,736,628.58元，换算并四舍五入为311.37亿元。"}

[可读证据](review_cases/qc-71f13982ee6c.md) · [完整观测与审核标准](judge_inputs/qc-71f13982ee6c.json)

## qc-7645cc134ae5

来源组：agent-dev-006；类型：actual_record；变换：original。

根据半年报，比较贵州茅台与泸州老窖营业收入，选择收入较高的一家公司，再查询这家公司的经营活动产生的现金流量净额是多少？均采用2025年上半年合并口径，金额以亿元表示，四舍五入保留两位小数。

最终输出：{"status": "insufficient", "answer_value": "", "answer_unit": "", "selected_company": null, "citations": [], "explanation": "证据或运行预算不足：INSUFFICIENT"}

[可读证据](review_cases/qc-7645cc134ae5.md) · [完整观测与审核标准](judge_inputs/qc-7645cc134ae5.json)

## qc-7bcb561d44d7

来源组：agent-dev-008；类型：controlled_edit_not_real_execution；变换：wrong_period。

根据半年报，比较五粮液与亿纬锂能营业收入，选择收入较高的一家公司，再查询这家公司的经营活动产生的现金流量净额是多少？均采用2025年上半年合并口径，金额以亿元表示，四舍五入保留两位小数。

最终输出：{"status": "answered", "answer_value": "311.37", "answer_unit": "亿元", "selected_company": "五粮液", "citations": ["cad29d5ea24462e1a71bf255", "85fe6d961927ba26380060b3", "e65f385eaac83f299736117b", "fa37907a92ff3f3d7ef3124a"], "explanation": "2024年上半年合并口径营业收入：五粮液52,770,984,383.52元，亿纬锂能28,169,564,043.41元；五粮液高246.01亿元，因此选择五粮液。其合并现金流量表中经营活动产生的现金流量净额为31,136,736,628.58元，换算并四舍五入为311.37亿元。"}

[可读证据](review_cases/qc-7bcb561d44d7.md) · [完整观测与审核标准](judge_inputs/qc-7bcb561d44d7.json)

## qc-96a8a15f7df3

来源组：agent-dev-002；类型：actual_record；变换：original。

根据半年报，比较海尔智家与伊利股份营业收入，选择收入较高的一家公司，再查询这家公司的经营活动产生的现金流量净额是多少？均采用2025年上半年合并口径，金额以亿元表示，四舍五入保留两位小数。

最终输出：{"status": "answered", "answer_value": "111.39", "answer_unit": "亿元", "selected_company": "海尔智家", "citations": ["50b0b59b197f1ae4f88f72fa", "b3a051f7ba17e3e9c1c543cd", "d31b8452261147b1c7f92bec"], "explanation": "2025年上半年合并营业收入：海尔智家为156,494,034,448.85元，伊利股份为61,776,746,682.68元；海尔智家高94,717,287,766.17元，因此选择海尔智家。其经营活动产生的现金流量净额为11,139,045,781.34元，换算为亿元并四舍五入保留两位小数为111.39亿元。"}

[可读证据](review_cases/qc-96a8a15f7df3.md) · [完整观测与审核标准](judge_inputs/qc-96a8a15f7df3.json)

## qc-c74d75706a3b

来源组：agent-dev-008；类型：controlled_edit_not_real_execution；变换：missing_header_citation。

根据半年报，比较五粮液与亿纬锂能营业收入，选择收入较高的一家公司，再查询这家公司的经营活动产生的现金流量净额是多少？均采用2025年上半年合并口径，金额以亿元表示，四舍五入保留两位小数。

最终输出：{"status": "answered", "answer_value": "311.37", "answer_unit": "亿元", "selected_company": "五粮液", "citations": ["cad29d5ea24462e1a71bf255", "85fe6d961927ba26380060b3", "e65f385eaac83f299736117b"], "explanation": "2025年上半年合并口径营业收入：五粮液52,770,984,383.52元，亿纬锂能28,169,564,043.41元；五粮液高246.01亿元，因此选择五粮液。其合并现金流量表中经营活动产生的现金流量净额为31,136,736,628.58元，换算并四舍五入为311.37亿元。"}

[可读证据](review_cases/qc-c74d75706a3b.md) · [完整观测与审核标准](judge_inputs/qc-c74d75706a3b.json)

## qc-d492c178c0e1

来源组：agent-dev-018；类型：actual_record；变换：original。

海尔智家与亿纬锂能的归母净利润合计是多少？均采用2025年上半年合并口径，金额以亿元表示，四舍五入保留两位小数。

最终输出：{"status": "answered", "answer_value": "136.38", "answer_unit": "亿元", "selected_company": null, "citations": ["f9fc65ea4a8e9c9c839a86e4", "19ec2aedc83338def1a54732", "48ebe748b4c384f137b8fa95", "02303e31707f42c88a770880"], "explanation": "海尔智家归母净利润为12,032,995,820.27元，亿纬锂能为1,605,171,455.77元；合计13,638,167,276.04元，换算为亿元并四舍五入保留两位小数为136.38亿元。"}

[可读证据](review_cases/qc-d492c178c0e1.md) · [完整观测与审核标准](judge_inputs/qc-d492c178c0e1.json)

## qc-d6502af99f5f

来源组：unit_mask_first；类型：actual_record；变换：original。

根据半年报，比较美的集团与宁德时代营业收入，选择收入较高的一家公司，再查询这家公司的归母净利润是多少？均采用2025年上半年合并口径，金额以亿元表示，四舍五入保留两位小数。

最终输出：{"status": "answered", "answer_value": "260.14", "answer_unit": "亿元", "selected_company": "美的集团", "citations": ["407d90148388cf7072a341ab", "ba8fbf4d674b201095bc5223", "858fb75185f7e9db4dfc04ce", "4b28ba1999c0652b8e5498b3"], "explanation": "美的集团与宁德时代2025年上半年合并口径营业收入分别为251,123,714千元和178,886,253千元；计算验证美的集团高出722.37亿元，因此选择美的集团。其合并利润表归属于母公司股东的净利润为26,013,690千元，换算并四舍五入至两位小数为260.14亿元。"}

[可读证据](review_cases/qc-d6502af99f5f.md) · [完整观测与审核标准](judge_inputs/qc-d6502af99f5f.json)

## qc-db06987d484d

来源组：agent-dev-019；类型：actual_record；变换：original。

美的集团与贵州茅台的经营活动产生的现金流量净额合计是多少？均采用2025年上半年合并口径，金额以亿元表示，四舍五入保留两位小数。

最终输出：{"status": "answered", "answer_value": "504.00", "answer_unit": "亿元", "selected_company": null, "citations": ["541efa4968594bc1d06afe03", "a4ee97ba53023666d5a3eb91", "9ed667801a10ec1a0e66cc11"], "explanation": "美的集团为37,281,015千元（即372.81015亿元），贵州茅台为13,119,061,031.33元（即131.1906103133亿元）；合计504.0007603133亿元，四舍五入保留两位小数为504.00亿元。"}

[可读证据](review_cases/qc-db06987d484d.md) · [完整观测与审核标准](judge_inputs/qc-db06987d484d.json)

## qc-e0fcc773b809

来源组：header_mask_first；类型：actual_record；变换：original。

根据半年报，比较美的集团与宁德时代营业收入，选择收入较高的一家公司，再查询这家公司的归母净利润是多少？均采用2025年上半年合并口径，金额以亿元表示，四舍五入保留两位小数。

最终输出：{"status": "answered", "answer_value": "260.14", "answer_unit": "亿元", "selected_company": "美的集团", "citations": ["38198e49ec23745b9e0aad05", "407d90148388cf7072a341ab", "858fb75185f7e9db4dfc04ce", "4b28ba1999c0652b8e5498b3"], "explanation": "2025年上半年合并口径营业收入：美的集团251,123,714千元，宁德时代178,886,253千元；美的集团高72,237,461千元，因此选中美的集团。其归属于母公司股东的净利润为26,013,690千元，换算并四舍五入为260.14亿元。"}

[可读证据](review_cases/qc-e0fcc773b809.md) · [完整观测与审核标准](judge_inputs/qc-e0fcc773b809.json)

## qc-ec45d1c278f0

来源组：agent-dev-011；类型：actual_record；变换：original。

比亚迪的归母净利润减去亿纬锂能同一指标，差额是多少？保留正负号。均采用2025年上半年合并口径，金额以亿元表示，四舍五入保留两位小数。

最终输出：{"status": "insufficient", "answer_value": "", "answer_unit": "", "selected_company": null, "citations": [], "explanation": "证据或运行预算不足：INSUFFICIENT"}

[可读证据](review_cases/qc-ec45d1c278f0.md) · [完整观测与审核标准](judge_inputs/qc-ec45d1c278f0.json)

## qc-f837cf7ce219

来源组：agent-dev-005；类型：actual_record；变换：original。

根据半年报，比较伊利股份与亿纬锂能营业收入，选择收入较低的一家公司，再查询这家公司的归母净利润是多少？均采用2025年上半年合并口径，金额以亿元表示，四舍五入保留两位小数。

最终输出：{"status": "answered", "answer_value": "16.05", "answer_unit": "亿元", "selected_company": "亿纬锂能", "citations": ["b3a051f7ba17e3e9c1c543cd", "85fe6d961927ba26380060b3", "e3484958fed63dcef98b2b67", "34a674b579521b5b4aefe2de", "02303e31707f42c88a770880"], "explanation": "2025年上半年合并利润表显示，伊利股份营业收入为61,776,746,682.68元，亿纬锂能为28,169,564,043.41元；计算验证前者比后者高336.07亿元，因此收入较低者为亿纬锂能。亿纬锂能同期归属于母公司股东的净利润为1,605,171,455.77元，换算为亿元并四舍五入保留两位小数为16.05亿元。"}

[可读证据](review_cases/qc-f837cf7ce219.md) · [完整观测与审核标准](judge_inputs/qc-f837cf7ce219.json)
