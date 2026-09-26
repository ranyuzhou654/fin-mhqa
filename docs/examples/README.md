# 示例轨迹

两条真实运行的轨迹，都是同一道 Dev 题目，配置为 [B3 + 核验 v2](../../configs/bench_b3_graph_repair_verifier_v2.json)。用演示脚本重放时不调用模型：

```bash
PYTHONPATH=src .venv/bin/python scripts/demo.py --replay docs/examples/f2_column_swap_caught
PYTHONPATH=src .venv/bin/python scripts/demo.py --replay docs/examples/clean_single_hop
```

| 示例 | 内容 |
| --- | --- |
| `f2_column_swap_caught` | 利润表中这一行的本期与上期数值被对调（模型不知情）。规划者按错位后的列取值；核验角色用「净利润 = 归母净利润 + 少数股东损益」发现期间错位并拒绝；规划者改用正确数值，最终答对 |
| `clean_single_hop` | 同一题在无故障条件下的轨迹，用来对照 |

每个目录包含 `trajectory.jsonl`（完整事件流）、`result.json`（发布结果与计数）和 `meta.json`（来源运行与说明）。
