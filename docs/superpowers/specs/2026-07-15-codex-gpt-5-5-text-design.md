# Codex GPT-5.5 / GPT-5.6 文本模型支持设计

## 目标

为 OpenAI 兼容接口增加以下 Codex 文本模型，使用 `source_type=codex` 账户调用 ChatGPT Codex `/backend-api/codex/responses`，同时支持纯文本和图片理解，并输出最终文本：

- `gpt-5.5`
- `gpt-5.6-terra`
- `gpt-5.6-luna`
- `gpt-5.6-sol`

支持的入口和模式：

- `POST /v1/chat/completions`：`stream=false` 与 `stream=true`。
- `POST /v1/responses`：`stream=false` 与 `stream=true`。
- 文本输入、单图输入、多图输入、Prompt 模板与图片混合输入。

本功能不改变现有 Web 文本模型 `gpt-5-5`、普通生图模型 `gpt-image-2` 或 Codex 生图模型 `codex-gpt-image-2` 的行为。

## 模型命名与路由

- `gpt-5-5`：保留为 ChatGPT Web 文本模型 slug，继续走 `/backend-api/conversation`。
- `gpt-5.5`、`gpt-5.6-terra`、`gpt-5.6-luna`、`gpt-5.6-sol`：Codex 文本模型，强制走 `/backend-api/codex/responses`。
- `codex-gpt-image-2`：继续使用现有 Codex 生图处理器，不与文本处理器合并。

协议入口通过精确匹配上述四个完整模型 ID 选择 Codex 文本处理器，不接受大小写、空格或未知 `gpt-5.6-*` 变体。其他模型继续沿用现有路由，禁止因为 Codex 账户不可用而把任何 Codex 文本模型回退到 Web 链路。

## 账户选择

Codex 文本模型只允许使用满足以下条件的账户：

- `source_type == "codex"`。
- 状态不是禁用或异常。
- 账户允许请求中的精确模型 ID；未配置 `allowed_models` 的 Codex 账户按现有不限制规则处理。
- 访问令牌能够通过现有刷新与远端信息校验。

本地不根据 `type` 硬编码 Free、Go、Plus、Pro、Business 或 Enterprise 的 GPT-5.6 套餐权限。账号可用范围由 `allowed_models` 与 Codex 上游共同判定，避免套餐规则变化后本地逻辑漂移。

没有合格账户时返回 HTTP 503，错误类型为 `service_unavailable`，错误码为 `model_account_unavailable`。不得选择 Web 账户兜底。

## 模型列表

`GET /v1/models` 针对四个 Codex 文本模型分别判断：仅当本地号池存在符合本地状态且允许该精确模型 ID 的 Codex 账户时追加对应模型。示例：

```json
{
  "id": "gpt-5.5",
  "object": "model",
  "created": 0,
  "owned_by": "chatgpt2api",
  "permission": [],
  "root": "gpt-5.5",
  "parent": null
}
```

模型列表判断不执行额外远端请求；真正调用时仍进行现有令牌与账户可用性校验。

## 输入契约

### Chat Completions

请求示例：

```json
{
  "model": "gpt-5.5",
  "messages": [
    {
      "role": "system",
      "content": "根据参考图和模板生成新的电商图片 Prompt。"
    },
    {
      "role": "user",
      "content": [
        {"type": "text", "text": "模板：主体必须保持一致，背景改为自然家居环境。"},
        {"type": "image_url", "image_url": {"url": "https://example.com/product.png"}},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}
      ]
    }
  ],
  "stream": false
}
```

支持：

- `content` 为字符串。
- `content` 为数组，其中包含 `text` 和 `image_url`。
- `image_url` 为字符串或 `{ "url": "..." }`。
- HTTP(S) URL 与 `data:image/...` URL。
- 多张图片，保持原始输入顺序。

### Responses

请求示例：

```json
{
  "model": "gpt-5.5",
  "instructions": "根据参考图和模板生成新的电商图片 Prompt。",
  "input": [
    {
      "role": "user",
      "content": [
        {"type": "input_text", "text": "模板：保持商品外观一致。"},
        {"type": "input_image", "image_url": "https://example.com/product.png"}
      ]
    }
  ],
  "stream": true
}
```

支持 `input` 字符串、消息数组、`input_text` 和 `input_image`。图片 URL 的限制与 Chat Completions 相同。

### 消息转换

- `system` 与 `developer` 消息中的文本按输入顺序合并到 Codex `instructions`。
- Responses 请求显式提供的 `instructions` 与消息中的系统指令合并，显式 `instructions` 位于前面。
- `user` 与 `assistant` 消息按顺序转换成 Codex `input`。
- 文本分别转换为 `input_text` 或 `output_text` 兼容内容；用户图片转换为 `input_image`。
- 图片不在本服务下载、转码或上传，URL 原样传给 Codex。
- 不接受空图片 URL、非 HTTP(S)/data-image 协议或 `file_id`。

## Codex 上游请求

新增独立的 Codex 文本请求方法。它复用现有 Codex Authorization、SSE 读取、失败日志摘要和 `UpstreamHTTPError`，但不改动 Codex 生图请求构造器。

上游请求形态：

```json
{
  "model": "gpt-5.5",
  "reasoning": {"effort": "high"},
  "instructions": "根据参考图和模板生成新的电商图片 Prompt。",
  "store": false,
  "input": [
    {
      "role": "user",
      "content": [
        {"type": "input_text", "text": "模板：保持商品外观一致。"},
        {"type": "input_image", "image_url": "https://example.com/product.png"}
      ]
    }
  ],
  "stream": true
}
```

请求地址固定为：

```text
POST https://chatgpt.com/backend-api/codex/responses
```

所有四个模型固定使用 `reasoning.effort=high`，不新增请求级配置，也不携带 `tools`、`tool_choice`、`size`、`quality` 或图片生成参数。上游请求中的 `model` 必须保持客户端请求的精确模型 ID，不允许统一改写成 `gpt-5.5`。

## SSE 解析与输出

底层解析器识别以下事件：

- `response.output_text.delta`：最终回答的文本增量。
- `response.output_text.done`：最终完整文本，可用于没有 delta 时的兜底。
- `response.completed`：成功终态。
- `response.failed`、`response.incomplete`、`error`：失败终态。

解析器只向协议层输出最终回答文本，不输出 reasoning、reasoning summary、工具事件或其他中间状态。若同时收到 delta 与 done，使用已发送文本消除重复；若只有 completed 的嵌套 output，则从终态响应中提取最终文本。

### Chat Completions 输出

- 非流式：返回现有 `chat.completion` 结构，`choices[0].message.content` 为最终文本。
- 流式：返回现有 `chat.completion.chunk` 结构，按文本增量输出，并以 `finish_reason="stop"` 结束。
- 响应中的 `model` 保持为客户端请求的精确 Codex 文本模型 ID。

### Responses 输出

- 非流式：返回现有 `response` 完成结构，包含一个 `message` / `output_text` 输出项。
- 流式：依次输出 Responses 兼容的 created、output item、content part、文本 delta/done、item done 和 completed 事件。
- 对外不暴露上游 Codex 原始事件对象。

## 工具与输出边界

首版只做文本生成与图片理解：

- 不开放函数工具、Web Search 或图片生成工具。
- 当任一 Codex 文本模型请求携带非空 `tools` 或 `tool_choice` 时返回 HTTP 400，错误信息使用实际模型 ID 并明确说明当前 Codex 文本通道不支持工具。
- 输出始终为文本，不返回图片、文件或推理过程。

## 错误处理

- 输入为空：HTTP 400。
- 图片 URL 非法、协议不支持或出现 `file_id`：HTTP 400。
- 请求携带工具：HTTP 400。
- 没有合格 Codex 账户：HTTP 503 `model_account_unavailable`。
- 上游 HTTP 401/403/429/5xx：保留现有结构化 `UpstreamHTTPError` 信息与 `retry_after`。
- `response.failed`、`response.incomplete` 或 `error`：转换为明确的 Codex 文本生成错误。
- SSE 正常结束但没有最终文本：视为失败，不返回空成功响应。
- 失败后可按现有无输出重试边界更换另一个合格 Codex 账户；一旦已经向客户端输出文本增量，不再换号重放，避免重复内容。

## 日志与安全

- 记录模型、账户邮箱、`source_type`、上游事件类型、终态、文本长度和图片数量。
- 不记录 Authorization、access token、完整 data URL、图片 base64 或完整敏感 Prompt。
- Prompt 预览沿用现有长度限制与日志策略。
- 执行账户邮箱继续通过内部字段供调用日志使用，不暴露给最终 OpenAI 兼容响应。

## 测试策略

采用测试先行方式覆盖：

1. 精确识别四个 Codex 文本模型，同时证明 `gpt-5-5`、大小写变体、带空格变体和未知 `gpt-5.6-*` 仍不进入 Codex 文本链路。
2. Chat Completions 同步与流式文本输出。
3. Responses 同步与流式文本输出。
4. system/developer 指令合并及 user/assistant 顺序保持。
5. HTTP URL、data URL、单图、多图和文本图片混排转换。
6. 非法图片、`file_id`、空输入和工具请求返回 400。
7. 只选择 `source_type=codex` 账户，Web 账户永不兜底。
8. 没有合格账户时返回 503。
9. Codex SSE delta、done、completed、failed、incomplete 和无文本终态解析。
10. `GET /v1/models` 按账户白名单分别暴露四个 Codex 文本模型。
11. 回归 `gpt-5-5`、`gpt-image-2` 与 `codex-gpt-image-2` 现有行为。

## 非目标

- 不支持 `gpt-5.6` 无后缀别名、`gpt-5.6-sol-pro` 或其他未显式登记的 GPT-5.6 变体。
- 不支持 multipart 图片上传或本地文件路径。
- 不支持 Codex 工具调用、Web Search、代码执行或图片生成。
- 不重构现有 Codex 生图链路。
- 不增加 GPT-5.5 前端专用配置页面。
- 不执行 Git 暂存、提交、推送或创建 PR。
