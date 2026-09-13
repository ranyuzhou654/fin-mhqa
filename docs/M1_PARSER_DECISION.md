# M1 下一决策：复杂表格解析的小样对比

状态：用户已确认，模型已下载，5 页试验已完成。结果见 [小样报告](DOCLING_PILOT_RESULTS.md)。

## 已观察到的问题

- 美的集团 PDF 第 56 页：多行表头被拆碎，部分列名未知。
- 美的集团 PDF 第 96 页：原解析器漏检的合并及公司利润表（原页有可见表线），文本对齐会截断列标题，当前保留原始正文，不把它冒充可靠结构表。
- 贵州茅台 PDF 第 8 页：二维分组表头，可用明确的规则展开；这类作为已修复对照样本。
- 比亚迪 PDF 第 65 页：无框利润表，目前通过文本对齐恢复；需要检查行名、期间、括号负数及单位。
- 格力电器 PDF 第 156 页：装饰页框与表线相连，需要版面区分；当前已去除单元格内的页眉干扰。

## 建议的有界试验

新增可替换抽取后端，先只比较以上 5 页。试用 Docling 默认布局模型加 TableFormer accurate 表结构模型，禁用 OCR（这些 PDF 已有文本层），本地运行。需要下载模型和安装独立依赖，不调用付费模型 API，不上传报告。

核对项目：标题与表格区域、行名完整性、每个金额字符、列名/期间/合并与母公司口径、空单元格与负数括号、来源页码。逐单元格对比原页面，并记录失败；若小样无明确改善，不扩展到 10 份全文。

Docling 不是已经验证的解决方案，当前仅建议进行受控对比。原 PyMuPDF/pdfplumber 流程和全部原始 PDF 保留，M1 验收门槛不降低。用户也可以选择继续仅使用规则解析。

官方依据：
- https://github.com/docling-project/docling/blob/main/docs/usage/advanced_options.md
- https://github.com/docling-project/docling/blob/main/docs/usage/model_catalog.md

官方文档提供本地模型预下载、artifacts_path 和 TableFormer accurate 配置；当前表结构模型设备支持含 CPU，MPS 受限。实际速度及中文财报效果需在本机实测。
