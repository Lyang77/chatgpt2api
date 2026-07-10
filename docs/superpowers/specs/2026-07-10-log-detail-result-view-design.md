# 日志详情结果视图设计

## 目标

将调用日志详情调整为结果优先的阅读界面：默认展示模型最终文本和生成图片，原始请求与完整响应只在排查时查看。

## 范围

- 修改日志详情前端，以及聊天/Responses 调用日志的图片地址采集。
- 复用现有 `ImageThumbnail` 和 `ImageLightbox`。
- 不修改 API、`SystemLog` 契约、SQLite 表或历史记录。

## 展示规则

1. 概览仅展示接口、模型、执行账号、状态、耗时、时间和 Key；不展示 `request_text`、`response_text`、图片 URL 或其他长字段。
2. 详情使用“结果内容 / 请求内容 / 原始数据”页签，默认进入“结果内容”。
3. 结果内容从 `response_text` 提取最终可读文本；标准聊天响应优先读取 `choices[*].message.content` 或 `output[*]` 的文本内容。无法识别结构时展示完整响应文本。
4. 请求图片写入并读取 `detail.request_urls`；返回图片写入并读取 `detail.response_image_urls`。两者均为稳定图片 URL，并可打开既有图片预览。
5. 请求内容用于查看实际 Prompt；原始数据按需折叠展示完整 `detail` JSON。

## 兼容性与错误处理

- `response_text` 不是 JSON 时按普通文本显示。
- JSON 中不存在可识别的最终文本时，按格式化 JSON 显示，避免丢失可用结果。
- 无图片或无文本时显示对应空态，不制造占位数据。
- 保留历史 `detail.urls`，但新详情页不以它推断聊天/Responses 图片，避免将网页搜索和引用链接渲染为图片。
