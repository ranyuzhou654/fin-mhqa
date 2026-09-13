# M2 等价证据 v2 验收结果

2026-09-13：冻结的 `source_equivalence_v2` 开发集完成 90 次真实 HTTP 查询，hybrid 主结果 Hit@5 为 **29/30（96.67%）**，通过原定 **>85%** 门槛。M2 的新版开发集验收为 `PASSED`；[v1 的 FAILED 结果](M2_FORMAL_RESULTS.md)完整保留。

| 模式 | v1 原标签 | v2 源页等价标签 |
| --- | --- | --- |
| Dense | 20/30（66.67%） | 27/30（90.00%） |
| BM25 | 16/30（53.33%） | 21/30（70.00%） |
| Hybrid | 23/30（76.67%） | 29/30（96.67%） |

**90 次查询的主结果排序全部与 v1 一致。** 将本次响应按 v1 标签重新计分，仍得到 20/30、16/30、23/30。分数变化来自获准的标签覆盖修正，没有检索算法或模型性能提升的证据；这也不是独立盲测或多跳答案正确率。

## 冻结与核验范围

标签和评测门禁先于 HTTP 评测提交于 `1710c6c`。候选发现扫描全部 30 题、10 份原 PDF 的 1,750 页及全量 chunks，不读取检索响应；审核者此前已见过 v1 失败结果，因此明确记录为代理源页审核，不声称盲审。

155 个题目—候选关联中，保留原标签 56 个，新增接受 66 个、拒绝 33 个；最终 122 个题目—gold 关联对应 114 个不同 chunk。14 题扩展，16 题不变。新增接受证据涉及 29 个源页，逐项保留来源、内容与渲染图哈希及审核理由。收入证据要求当前合并口径、期间与原单位可核对，缺表头、缺单位或只含局部业务金额的候选不纳入新增标签。详见 [v2 标签规则与冻结凭据](RETRIEVAL_LABELS_V2.md)。

候选数据仍为 9,322 条，正式索引审核状态仍为 `ACCEPTED`；问题文本、索引、模型和检索参数均未修改。沿用本地 bge-m3 与 bge-reranker-v2-m3、CPU float32、batch size 4、最大长度 1,024；Dense 为 CLS + L2，RRF 为 60，每种模式均重排 30 个候选后取 5 个主结果。附加父表不计命中。原有 258 条 embedding 截断（2.77%）仍保留标记与完整源文本。

## 剩余失败与验证

Hybrid 唯一未命中为 `retrieval-dev-28`：五粮液经营活动现金流增加原因。即使补入源页 12 的注 2 等价正文，主结果仍未召回该说明；[原始失败响应](evidence/retrieval_v2/hybrid_failure.json)保留，未把相邻表格命中当成原因说明命中。后续 Agent 的证据读取仍需处理注释与被解释对象的关联。

- 标签绑定及评测门禁测试：15 项通过。
- 实际 HTTP：90/90 返回 200，评测进程退出码 0；开始与结束的绑定及数据校验通过。
- 串行请求耗时合计约 18.7 分钟；单次中位耗时 Dense 12.38 秒、BM25 10.33 秒、Hybrid 10.99 秒。
- 结束时再次确认服务就绪、正式数据 9,322 条，随后正常关闭服务。

运行命令（复跑须使用尚不存在的输出文件名）：

```bash
.venv-retrieval/bin/python -m uvicorn finmhqa.retrieval.server:create_app \
  --factory --host 127.0.0.1 --port 8100
# 服务就绪后在另一终端执行：
.venv-retrieval/bin/python scripts/evaluate_retrieval.py \
  --url http://127.0.0.1:8100 \
  --queries data/eval/retrieval_dev30.v2.bound.jsonl \
  --out data/eval/retrieval_dev30.v2.recheck.json
```

## 证据与下一步

Git 中保存 [结果与 v1 对照摘要](evidence/retrieval_v2/summary.json)、[验证与文件哈希](evidence/retrieval_v2/validation.json)、[健康状态](evidence/retrieval_v2/health.json)及冻结标签。完整结果、90 条请求响应和运行日志分别保留于本地 `data/eval/retrieval_dev30.v2.results.json`、`data/eval/retrieval_dev30.v2.results.requests.jsonl`、`data/retrieval_v2_*.log`。

原数据、v1 标签、结果与请求响应的哈希复核未变。历史凭据中的一份服务日志使用了换行归一化文本哈希；本次已验证该方法并在验证文件中补记原始字节哈希，原凭据及日志未改写。

下一阶段是 M3 多跳 Agent；尚未选择或调用教师 API。需先由用户确定教师模型及服务商，见 [M3 决策说明](M3_DECISION.md)。
