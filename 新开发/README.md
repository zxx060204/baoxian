# 新开发

> 状态：现行
> 适用日期：2026-11-19
> 不要据此实施：否

现行后端 API 和 Worker 使用 Python，前端使用 TypeScript。项目范围、验收边界及未决问题以 [PROJECT_STATE.md](../PROJECT_STATE.md) 为准。

## 运行与测试

从仓库根目录进入 `新开发/`，准备 Node.js 24、pnpm（版本见 `package.json`）、Python 3.11 及以上和 Docker。

1. 按 [Docker 说明](infrastructure/docker/README.md)启动 MySQL、Redis 和 MinIO。空卷会初始化两套业务库和两套隔离测试库。
2. 将工作区的 `.env.example` 复制为 `.env`。如只想试用本地协会样本，将 `ASSOCIATION_SOURCE` 设为 `fixture`。
3. 运行 `pnpm install --frozen-lockfile` 和 `python -m venv .venv-python`。Windows 使用 `.venv-python\Scripts\python.exe -m pip install ./apps/backend-python`，Linux 或 macOS 使用 `.venv-python/bin/python -m pip install ./apps/backend-python`。
4. 在三个终端分别运行 `pnpm dev:api`、`pnpm dev:worker` 与 `pnpm dev:web`。浏览器打开 `http://127.0.0.1:5173`，前端默认代理到 `127.0.0.1:3001`。

- 前端测试：`pnpm test`；类型检查：`pnpm typecheck`；构建：`pnpm build:web`。
- 后端完整测试：进入 `apps/backend-python`，运行 `..\..\.venv-python\Scripts\python.exe -m unittest discover -s tests -v`。集成测试需要隔离 MySQL、Redis 和 MinIO；运行前核对目标资源。
- Worker 会消费配置队列并注册周期任务；启动前核对 `.env`、在途任务和计划。`ASSOCIATION_SOURCE=fixture` 使用本地样本，默认 `real` 访问协会来源。
- [接口与副作用映射](docs/0.项目整体/后端Python接口与测试行为映射.md)记录 API 契约；友邦和国寿真实来源覆盖及浏览器验收仍待完成。

## 数值表 PDF 解析

经项目 API 鉴权后，请求 `GET /api/documents/{document_id}/layout`，按需将已存的费率表或现金价值表 PDF 转成 `mapping-input-v1`。数据库的 `rate_table` 和 `cash_value_table` 在此 layout 中分别为 `premium_rate` 和 `cash_value`。输入文件通过数据库记录的 SHA-256 校验。

表格内容为 `tables[].grid` 二维数组。位置上的 `[原文, 跨行数, 跨列数]` 记录合并锚点及普通格，`-1` 表示被合并覆盖，`null` 表示未记录的位置。空字符串和文本形式的零继续区分。页面、坐标轴、上下文和续表关系沿用旧版 layout。结构为 `partial` 或 `fragment` 的表仍需复核，片段可保留原始文字和位置。此入口的范围是原生 PDF；需 OCR 的原件返回解析失败原因。结果按请求返回 JSON。

## 条款与说明书 PDF 解析

经同一 API 鉴权后，请求 `GET /api/documents/{document_id}/parsed-content`。接口按原件哈希校验后返回 `document-content-v1`，包括页面状态、章节、正文段落、分块和表格。分块保留原页、坐标和原文字符区间。说明书利益演示表走表格识别，按上述二维网格格式返回，分块中的 `table_reference` 通过 `table_id` 指向表格。原页文字仍可用于溯源。结果通过接口返回，不新增写库路径。

含扫描页的文档会尝试旧版 OCR。旧版 RapidOCR 需 Python 3.11 或 3.12，并安装 `apps/backend-python[ocr]`。当前 Python 3.14 服务未装该依赖，需 OCR 的扫描页标为 `needs_ocr`，页面标记 `ocr_dependency_missing`。解析结构及来源可供核查，业务表头含义和跨页归属仍需复核。

## 产品资料一键解析

「产品 → 详情 → 资料解析」支持一键处理该产品当前已下载的四类 PDF。发起后逐份解析并将 layout JSON 保存到 MinIO 原有桶的 `layouts/v1/{documentId}/{type}/{sourceSha256}.json`。单份失败后继续处理其余资料，重复执行复用已保存结果，单份重解析可更新结果。关闭并重新打开详情后可直接查看已保存的 layout。接口见[后端接口契约](docs/0.项目整体/后端Python接口与测试行为映射.md)。

## 工作区目录

- `apps/backend-python/`：现行 Python API、Worker、测试和测试样本。
- `apps/web/`：React 中台。
- `infrastructure/docker/`：本地依赖服务及说明。

目前没有通用数据库迁移工具。新建数据库使用 Docker 初始结构，其中已包含产品身份与协会记录归组。


业务方向与约束见 [总体架构](docs/0.项目整体/总体架构.md)、[P1/P2 实现规格](docs/1.信息接入与资料获取/P1-P2-Spec.md) 和根目录项目状态。
