# benchmark_v1 冻结与使用（S5）

2026-09-23 冻结。Dev 50 道用于调试和架构选择，Test 100 道只用于最终报告。用户抽查 20 道 Test 题，题面清晰度和答案正确性全部通过，结论记录在 manifest 中。

## 文件

| 位置 | 内容 | 是否进 git |
| --- | --- | --- |
| `data/bench/benchmark_v1/` | `dev_questions.jsonl`、`test_questions.jsonl`（只有 id 和题面）、`manifest.json` | 是，见 [evidence](evidence/benchmark_v1_frozen/) |
| `data/bench/benchmark_v1_gold/` | `dev_gold.jsonl`、`test_gold.jsonl` | 只提交 Dev；Test 答案只在 manifest 中记录 SHA256 |
| `configs/benchmark_v1_registry.json` | 允许跑 Test 的配置登记表，目前为空 | 是 |
| `data/bench/benchmark_v1/test_runs.jsonl` | 每次 Test 运行的开始和结束记录，只追加 | 运行后产生 |

manifest 记录了题面和答案的哈希，以及事实台账、生成报告、S4 汇总、抽查页和全部生成与质检代码的哈希。

## 使用规则（由代码强制执行）

- 运行器用 `--benchmark dev|test` 读取冻结的题面，读取前校验哈希；题面被改动时拒绝运行。
- 跑 Test 必须同时满足三条：加 `--final`；`--config` 指定的配置文件的 SHA256 已写入登记表；不能用 `--only` 只跑部分题目。配置在登记后又被修改，同样会被拒绝。
- 每次 Test 运行都会在 `test_runs.jsonl` 中追加开始和结束记录，不能挑选运行结果。
- 登记表只在 Dev 上选定最终候选配置之后才添加条目，每条要写明日期和理由。

```bash
.venv/bin/python scripts/run_agent_dev.py --benchmark dev --config <配置> --out data/bench/runs/<名称>
.venv/bin/python scripts/evaluate_benchmark.py --runs <运行1> <运行2> <运行3> --baseline <基线运行…> --out <报告.json>
```

## 评分（[scoring.py](../src/finmhqa/bench/scoring.py)）

- **正确**：
  - 可回答题：状态为 answered，数值相等（2.6 与 2.60 视为相等，但 2.61 不相等），单位一致（全角 ％ 视为 %），需要选公司的题还要选对公司。
  - 无法回答题：状态为 insufficient。
  - 报错或没有运行的题一律算错，并保留在分母中。
- **拒答**：T6 的拒答召回率；可回答题上的误拒率。
- **依据**：
  - 引用有效性：引用的 ID 都在实际观测中出现过。
  - 各跳证据覆盖：答案引用中，是否有块就是该事实所在的块，或者同一份报告中含有该原文数值的块。
- **成本**：平均模型调用次数、平均工具调用次数、token 数、平均与 P95 耗时。
- **多次运行**：报告每次的准确率、均值、标准差，以及始终答对和始终答错的题数。
- **配对比较**：每道题取多次运行的平均正确率，对题目做 bootstrap（10,000 次），给出准确率差值的 95% 区间，以及各自更好的题数。

## 验证

- 测试 174 项通过、1 项跳过。新增测试覆盖：登记与 `--final` 保护、题面篡改、数值、单位与选公司规则、拒答、引用与各跳覆盖、分母、配对 bootstrap。
- 命令行实测了 4 种违规用法，全部在调用模型之前就被拒绝：不加 `--final` 跑 Test、未登记的配置、在 Dev 上加 `--final`、Test 加 `--only`。
- 端到端冒烟：用 DeepSeek（teacher_v2 配置）在 Dev 上跑 1 道母公司口径陷阱题，答出 9.28 亿元，与参考答案一致；审计 1/1 通过；评分器把其余 49 道没运行的题计为错误。

## 下一步（S6）

在 Dev 上跑基线 B0–B3，每个配置 3 次。现有配置使用 calculator v1，无法回答比率题，因此需要先新建使用 calculator v2 的基线配置；B0（闭卷）和 B1（检索一次就回答）还需要实现运行方式。
