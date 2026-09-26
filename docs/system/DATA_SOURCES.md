# 2025H1 数据来源清单

本项目选择家电、新能源及消费行业 10 家 A 股公司，兼顾同行比较和跨行业多跳问题。统一使用 2025 年当期中文半年报全文；后续修订不混入这一历史披露快照。五粮液记录标为更新前，不代表截至当前的最新财务口径。

| 公司 | 代码 | 行业 | 页数 | 披露日期 | 来源 |
|---|---|---|---:|---|---|
| 美的集团 | 000333 | 家电 | 205 | 2025-08-29 | [PDF](https://static.cninfo.com.cn/finalpage/2025-08-30/1224626720.PDF) |
| 格力电器 | 000651 | 家电 | 189 | 2025-08-28 | [PDF](https://static.cninfo.com.cn/finalpage/2025-08-29/1224613604.PDF) |
| 海尔智家 | 600690 | 家电 | 197 | 2025-08-28 | [PDF](https://static.cninfo.com.cn/finalpage/2025-08-29/1224609917.PDF) |
| 比亚迪 | 002594 | 新能源汽车 | 200 | 2025-08-29 | [PDF](https://static.cninfo.com.cn/finalpage/2025-08-30/1224626653.PDF) |
| 宁德时代 | 300750 | 电池 | 173 | 2025-07-30 | [PDF](https://static.cninfo.com.cn/finalpage/2025-07-31/1224342874.PDF) |
| 亿纬锂能 | 300014 | 电池 | 195 | 2025-08-21 | [PDF](https://static.cninfo.com.cn/finalpage/2025-08-22/1224535979.PDF) |
| 贵州茅台 | 600519 | 白酒 | 103 | 2025-08-12 | [PDF](https://static.cninfo.com.cn/finalpage/2025-08-13/1224462930.PDF) |
| 五粮液 | 000858 | 白酒 | 126 | 2025-08-27 | [PDF](https://static.cninfo.com.cn/finalpage/2025-08-28/1224596951.PDF) |
| 泸州老窖 | 000568 | 白酒 | 147 | 2025-08-29 | [PDF](https://static.cninfo.com.cn/finalpage/2025-08-30/1224618711.PDF) |
| 伊利股份 | 600887 | 乳制品 | 215 | 2025-08-28 | [PDF](https://static.cninfo.com.cn/finalpage/2025-08-29/1224611750.pdf) |

共 10 份，1750 页。原文件位于 `data/raw_pdf/`，完整 SHA256/下载时间在 `data/source_manifest.json` 及逐文件 `.source.json` 中。

## Tokenizer

BAAI/bge-m3，固定 revision `5617a9f61b028005a4858fdac845db406aefb181`。仅下载 tokenizer.json、tokenizer_config.json、special_tokens_map.json、sentencepiece.bpe.model，存放 `data/tokenizers/bge-m3/`。逐文件哈希见其 provenance.json。未下载 embedding 或 reranker 模型权重。
