# Codex 文本错误诊断设计

## 目标

当 Codex 文本上游以 `error`、`response.failed` 或 `response.incomplete` 事件终止时，对外继续返回安全、通用的错误信息，同时在后台调用日志中持久化足以排查问题的结构化诊断信息。

本次只补齐错误可观测性，不改变模型路由、账户选择、重试次数、正常响应或客户端错误协议。

## 当前问题

Codex 文本事件解析器当前只保留终止事件的 `type`，随后抛出 `Codex text generation failed: <event_type>`。上游事件中的错误类型、错误码、错误消息、未完成原因和响应标识在调用日志落库前已经丢失。

现有安全测试又要求异常字符串不能包含上游原始内容，因此不能简单地把上游 payload 拼进 `str(exc)`；否则后台日志变详细的同时，也可能通过兼容接口把敏感内容返回给调用方。

## 方案

新增 Codex 文本专用结构化异常，分离两个通道：

- 公开通道：`str(exc)` 只保留现有通用错误，不包含上游消息。
- 诊断通道：异常对象携带 `diagnostic_detail`，由调用日志捕获后写入 `detail.upstream_error`。

诊断结构只包含以下允许字段：

```json
{
  "event_type": "error",
  "type": "upstream_error",
  "code": "rate_limit_exceeded",
  "message": "sanitized and truncated upstream message",
  "response_id": "resp_...",
  "incomplete_reason": "max_output_tokens"
}
```

字段不存在时不写空值。错误信息和标识统一经过现有 Codex 日志清洗逻辑，移除 Bearer token、data URL 等内容并限制长度。不得保存完整事件、Authorization、访问令牌、图片 base64、完整请求体或推理内容。

## 数据流

1. Codex SSE 解析器产出上游事件。
2. 终止事件解析器从 `event.error`、`event.response.error`、`event.response.incomplete_details` 及事件/响应标识中提取允许字段。
3. 解析器抛出专用异常：公开字符串保持通用，结构化诊断附在异常属性中。
4. `LoggedCall` 捕获异常并继续按原方式生成客户端错误响应，同时把诊断属性写入调用日志的 `upstream_error`。
5. 日志详情接口与原始数据标签页沿用现有 `detail` 透传行为，无需新增接口。

## 错误与安全边界

- HTTP 层返回内容不得新增上游 `message`、`code` 或原始事件。
- 后台调用日志只保存允许字段，且所有字符串必须先清洗、再截断。
- 未识别的上游对象不得整体序列化到日志。
- 如果诊断提取失败，仍按当前通用错误失败，不得影响原异常处理。
- `error`、`response.failed`、`response.incomplete` 以及带失败状态的 `response.completed` 都使用同一诊断契约。

## 测试策略

采用测试先行：

1. 先新增失败测试，证明终止事件的结构化诊断能够保存在异常属性中，而异常字符串仍不包含敏感上游消息。
2. 覆盖顶层 `error`、嵌套 `response.error`、`incomplete_details` 和响应标识提取。
3. 新增调用日志测试，证明 `diagnostic_detail` 会写入 `detail.upstream_error`，普通异常不新增该字段。
4. 验证 Bearer token、data URL 和超长消息经过清洗/截断。
5. 运行 Codex 文本传输、调用日志及相关协议测试，确认正常响应、重试和客户端错误保持不变。

## 验收标准

- 新失败记录的 `detail.error` 仍为安全的通用错误。
- 同一记录包含结构化 `detail.upstream_error`，能看到上游错误类型、错误码、清洗后的消息以及可用的响应标识。
- 客户端响应不泄露 `upstream_error`。
- 三类 Codex 失败终止事件和失败状态的 `response.completed` 均有回归覆盖。
- 现有 Codex 文本和日志相关测试通过。

## 非目标

- 不补写已经产生的历史日志。
- 不修改上游 Codex 服务或 ai-app 的错误模型。
- 不新增日志搜索条件或前端专用展示组件。
- 不改变账户切换与重试策略。
- 不推送或创建 PR。
