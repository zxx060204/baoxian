# 配置边界

采集域名、入口和资料类型位于 src/insurance_rag/acquisition/*.json；运行参数由 CLI 传入并保存运行快照。

parsing.json 保持原生基线。parsing_advanced.json 默认关闭 OCR，开启 whitespace XY-cut 和表格候选抽取；针对图片页可明确设置 ocr_mode=auto 使用 CPU OCR，通过 parse --config 载入。所有配置参与 parse_key；未知字段、模式或无效阈值会报错。

增强配置包含 OCR 模式、DPI、置信度与渲染像素上限。扫描超大页面先检查像素限制；不会自动降低 DPI 后冒充完整识别。置信度阈值只用于标记复核，不用于修正或删除金融数值。表格表头、坐标轴、续表和数值均保持待审核。

命令参数见 `python scripts/parse.py --help`；配置字段以 `src/insurance_rag/parsing/models.py` 中的 ParseConfig 为准。

parsing_evaluation_sample.json 仅用于扩展评估：page_selection=first_last，保留原件页码并标记未处理页。生产解析默认 page_selection=all，不把抽样作为全文完成。
