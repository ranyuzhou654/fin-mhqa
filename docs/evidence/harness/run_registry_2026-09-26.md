# 运行登记表快照

由 `scripts/run_registry.py` 生成；Test 运行不列准确率。

| run | category | status | fault | items | accuracy | errors_by_class | mean_model_calls | tokens_per_item | git_commit |
|---|---|---|---|---|---|---|---|---|---|
| aborted_dev_b1_single_retrieval_r2 | aborted | UNFINISHED |  | 27 | 0.4074 | {"not_run": 4} | 0.852 | 4591 |  |
| aborted_dev_b3_graph_repair_r3 | aborted | UNFINISHED |  | 26 | 0.8462 | {"not_run": 4} | 4.692 | 71490 |  |
| dev_b0_closed_book_r1 | valid | LEGACY |  | 50 | 0.22 | {} | 1 | 1896 |  |
| dev_b0_closed_book_r2 | valid | LEGACY |  | 50 | 0.18 | {} | 1 | 2238 |  |
| dev_b0_closed_book_r3 | valid | LEGACY |  | 50 | 0.16 | {} | 1 | 2851 |  |
| dev_b1_single_retrieval_r1 | valid | LEGACY |  | 50 | 0.62 | {} | 1 | 7528 |  |
| dev_b1_single_retrieval_r2 | valid | LEGACY |  | 50 | 0.6 | {} | 1 | 7411 |  |
| dev_b1_single_retrieval_r3 | valid | LEGACY |  | 50 | 0.62 | {} | 1 | 7473 |  |
| dev_b2_flat_r1 | valid | LEGACY |  | 50 | 0.9 | {"agent": 3, "transport": 1} | 3.98 | 51668 |  |
| dev_b2_flat_r2 | valid | LEGACY |  | 50 | 0.92 | {"agent": 4} | 4.44 | 58889 |  |
| dev_b2_flat_r3 | valid | LEGACY |  | 50 | 0.96 | {"agent": 1} | 4.28 | 54766 |  |
| dev_b2_flat_repair_r1 | valid | LEGACY |  | 50 | 1.0 | {} | 4.28 | 55508 |  |
| dev_b2_flat_repair_r2 | valid | LEGACY |  | 50 | 1.0 | {} | 4.38 | 58476 |  |
| dev_b2_flat_repair_r3 | valid | LEGACY |  | 50 | 0.96 | {} | 4.38 | 62285 |  |
| dev_b3_graph_r1 | valid | LEGACY |  | 50 | 0.96 | {"agent": 1} | 5.22 | 79128 |  |
| dev_b3_graph_r2 | valid | LEGACY |  | 50 | 0.96 | {"agent": 2} | 5.24 | 82592 |  |
| dev_b3_graph_r3 | valid | LEGACY |  | 50 | 0.88 | {"agent": 5} | 5.18 | 77928 |  |
| dev_b3_graph_repair_r1 | valid | LEGACY |  | 50 | 0.94 | {} | 5.58 | 94517 |  |
| dev_b3_graph_repair_r2 | valid | LEGACY |  | 50 | 0.96 | {} | 5.18 | 84307 |  |
| dev_b3_graph_repair_r3 | valid | LEGACY |  | 50 | 1.0 | {} | 5.2 | 81182 |  |
| dev_b3_graph_repair_verifier_v2_r1 | valid | LEGACY |  | 50 | 0.94 | {"agent": 2} | 6.26 | 111740 |  |
| dev_b3_graph_repair_verifier_v2_r2 | valid | LEGACY |  | 50 | 0.98 | {"agent": 1} | 6.08 | 105903 |  |
| dev_b3_graph_repair_verifier_v2_r3 | valid | LEGACY |  | 50 | 0.96 | {} | 6.1 | 104842 |  |
| fault_F1_b2_flat_repair_r1 | valid | LEGACY | F1 | 44 | 0.9545 | {"transport": 1} | 4.795 | 69000 |  |
| fault_F1_b3_graph_repair_r1 | valid | LEGACY | F1 | 44 | 0.9545 | {} | 6.295 | 110343 |  |
| fault_F1_b3_graph_repair_v2_planner_v2_r1 | valid | LEGACY | F1 | 44 | 0.9545 | {} | 6.932 | 131176 |  |
| fault_F1_b3_graph_repair_verifier_v2_r1 | valid | LEGACY | F1 | 44 | 0.9773 | {} | 6.841 | 132311 |  |
| fault_F2_b2_flat_repair_r1 | valid | LEGACY | F2 | 44 | 0.6136 | {"transport": 3} | 4.636 | 73053 |  |
| fault_F2_b3_graph_repair_r1 | valid | LEGACY | F2 | 44 | 0.7045 | {} | 6.159 | 113917 |  |
| fault_F2_b3_graph_repair_v2_planner_v2_r1 | valid | LEGACY | F2 | 44 | 0.8182 | {"transport": 1} | 7.386 | 154603 |  |
| fault_F2_b3_graph_repair_verifier_v2_r1 | valid | LEGACY | F2 | 44 | 0.8182 | {"agent": 2} | 7.455 | 152102 |  |
| fault_F3_b2_flat_repair_r1 | valid | LEGACY | F3 | 44 | 0.7727 | {"transport": 1} | 4.864 | 80070 |  |
| fault_F3_b3_graph_repair_r1 | valid | LEGACY | F3 | 44 | 0.7955 | {} | 7.909 | 174845 |  |
| fault_F3_b3_graph_repair_v2_planner_v2_r1 | valid | LEGACY | F3 | 44 | 0.9545 | {"transport": 1} | 6.841 | 144888 |  |
| fault_F3_b3_graph_repair_verifier_v2_r1 | valid | LEGACY | F3 | 44 | 0.9091 | {} | 6.795 | 145295 |  |
| invalid_dev_b2_flat_r1 | invalid | LEGACY |  | 50 | 0.56 | {} | 4.02 | 34296 |  |
| invalid_dev_b2_flat_r2 | invalid | LEGACY |  | 50 | 0.26 | {} | 3.16 | 11318 |  |
| invalid_dev_b2_flat_r3 | invalid | LEGACY |  | 50 | 0.18 | {} | 3.22 | 12237 |  |
| invalid_dev_b3_graph_r1 | invalid | LEGACY |  | 50 | 0.26 | {} | 3.38 | 16751 |  |
| invalid_dev_b3_graph_r2 | invalid | LEGACY |  | 50 | 0.44 | {} | 4.02 | 39060 |  |
| invalid_dev_b3_graph_r3 | invalid | LEGACY |  | 50 | 0.34 | {"quota": 32} | 1.92 | 21637 |  |
| invalid_quota_dev_b3_graph_repair_v2_planner_v2_r1 | invalid_quota | LEGACY |  | 50 | 0.26 | {"quota": 36, "transport": 1} | 2.02 | 28604 |  |
| invalid_quota_dev_b3_graph_repair_v2_planner_v2_r2 | invalid_quota | LEGACY |  | 50 | 0.0 | {"quota": 50} | 0 | 0 |  |
| invalid_quota_dev_b3_graph_repair_v2_planner_v2_r3 | invalid_quota | LEGACY |  | 50 | 0.0 | {"quota": 50} | 0 | 0 |  |
| superseded_zen_dev_b0_closed_book_r1 | superseded_zen | LEGACY |  | 50 | 0.1 | {} | 1 | 422 |  |
| superseded_zen_dev_b0_closed_book_r2 | superseded_zen | LEGACY |  | 50 | 0.1 | {} | 1 | 423 |  |
| superseded_zen_dev_b0_closed_book_r3 | superseded_zen | LEGACY |  | 50 | 0.1 | {} | 1 | 422 |  |
| superseded_zen_dev_b1_single_retrieval_r1 | superseded_zen | LEGACY |  | 50 | 0.44 | {"agent": 1} | 1 | 5656 |  |
| superseded_zen_dev_b1_single_retrieval_r2 | superseded_zen | LEGACY |  | 50 | 0.44 | {"agent": 1} | 1 | 5662 |  |
| superseded_zen_dev_b1_single_retrieval_r3 | superseded_zen | LEGACY |  | 50 | 0.46 | {"agent": 1} | 1 | 6231 |  |
