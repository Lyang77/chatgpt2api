# 统一调用日志诊断信息设计

## 目标

为图片和文本调用日志补充安全、结构化的请求参数摘要，并在日志详情中直接展示关键请求参数与执行诊断信息，减少排查问题时反查源码或原始载荷的成本。

本次覆盖 `/v1/images/generations`、`/v1/images/edits`、`/api/image-tasks/generations`、`/api/image-tasks/edits`、`/v1/chat/completions`、`/v1/responses`、`/v1/messages`、`/v1/search`、`/v1/ppt/generations` 和 `/v1/psd/generations`。

## 数据契约

在现有日志 `detail` 中新增 `request_meta` 对象。已有 `endpoint`、`model`、`request_text`、`request_shape`、`request_urls`、状态、耗时和响应字段保持不变，历史日志无需迁移。

### 图片请求参数

图片接口按实际请求记录以下字段：

- `mode`：`generate` 或 `edit`
- `size`
- `quality`
- `n`
- `output_format`
- `response_format`
- `reference_image_count`
- `mask_image_count`
- `client_task_id`
- `stream`

同步图片接口和异步图片任务必须使用同一提取函数生成 `request_meta`。尺寸继续按现有业务链路传给模型，本次仅补充日志投影，不改变生成语义。

### 文本请求参数

文本接口仅记录诊断所需的安全摘要：

- `stream`
- `message_count`、`input_item_count`
- `role_counts`
- `tool_count`
- `image_input_count`
- `tool_choice_type`
- `response_format_type`
- `reasoning_effort`
- `max_tokens`、`max_completion_tokens`、`max_output_tokens`
- `temperature`、`top_p`
- `store`
- `modalities`
- `prompt_chars`、`input_chars`、`system_chars`
- PPT/PSD 的 `client_task_id` 和 `reference_image_count`

字段只在请求中存在且值可安全归一化时写入。计数字段记录数量，不复制消息、工具或图片内容。

## 安全边界

统一提取器采用允许列表，不递归保存未知字段。以下信息不得进入 `request_meta`：

- Authorization、API Key、Cookie、访问令牌和账号凭据
- Base64、Data URL、图片二进制和完整远程图片 URL
- 完整消息数组、完整工具定义、函数参数 Schema 和原始请求 JSON
- Prompt 或响应正文副本

Prompt、响应正文和请求图片仍由现有 `request_text`、`response_text`、`request_urls` 契约负责，并继续使用现有截断与图片存储逻辑。

`tool_choice_type` 只保留字符串值或对象的 `type`；`response_format_type` 只保留格式对象的 `type`；`modalities` 只保留字符串列表。所有异常、复杂或超长值直接省略，不回退为 `str(raw_value)`。

## 日志写入架构

新增独立纯函数模块负责：

1. 图片载荷生成安全 `request_meta`。
2. 按 OpenAI Chat Completions、Responses、Anthropic Messages、Search、PPT/PSD 的请求形态生成文本摘要。
3. 统计消息角色、输入项、工具和图片数量。

`LoggedCall` 新增可选 `request_meta`，在成功、失败和流式终态中统一写入。图片子任务日志模板和 `ImageTaskService` 最终日志也携带相同摘要，确保同步接口、异步任务、成功和失败分支均不丢失请求参数。

执行诊断继续使用现有平铺字段，不复制进 `request_meta`。详情页按存在性展示：`requested_model`、`effective_model`、`fallback_reason`、`stage`、`retry_count`、`queue_wait_ms`、`batch_id`、`image_index/image_total`、`conversation_id`、`cache_hit`、`actual_image_count`、`completion_reason` 和 `error`。字段在当前执行路径不可得时显示 `-`，不得伪造默认值。

## 日志详情 UI

详情顶部保留当前基础信息卡：接口、模型、执行账号、状态、耗时、开始时间、结束时间和 Key。

在“请求内容”页签顶部新增“请求参数”卡片，根据 `request_meta` 的稳定字段映射为中文标签并使用紧凑网格展示。数组和对象只展示已经归一化的短值，例如角色计数显示为 `user: 2，assistant: 1`。

在基础信息卡下新增“执行诊断”卡片，只展示当前日志实际存在的诊断字段。错误信息允许换行和复制，不使用截断单元格隐藏关键内容。没有任何扩展诊断字段时不渲染该卡片。

“原始数据”页签继续展示完整 `detail`，供进一步核对；弹窗继续只有一个纵向滚动条。

## 兼容与异常处理

- 历史日志没有 `request_meta` 时，详情页保持当前展示，不报错。
- 提取器遇到非预期类型时省略该字段，不能影响真实调用。
- 日志写入失败仍沿用现有容错，不阻断模型请求。
- 不新增数据库列；`request_meta` 随 `detail_json` 保存。
- 列表接口仍省略大文本和请求图片，但保留体积很小的 `request_meta`，方便后续扩展列表诊断，不改变当前列表列。

## 测试

- 图片生成、图片编辑、异步图片任务的 `request_meta` 字段一致性。
- Chat Completions、Responses、Messages、Search、PPT/PSD 的安全字段提取。
- Authorization、API Key、Cookie、Base64、完整工具定义和未知嵌套对象不会进入结果。
- `LoggedCall` 的成功、失败和流式日志均保存 `request_meta`。
- 图片任务成功、失败日志均保存尺寸、质量和模式。
- 日志详情能展示请求参数和执行诊断；历史日志无 `request_meta` 时兼容。
- 运行后端定向测试、Web 定向测试和生产构建。

## 验收标准

1. 新产生的图片日志详情可直接看到尺寸、质量、模式、数量、输出格式和参考图数量。
2. 新产生的文本日志详情可直接看到流式、消息/工具/图片数量及主要生成参数。
3. 请求模型与实际模型、回退、阶段、重试、排队、会话、缓存和完成原因等已有诊断字段可直接查看。
4. 敏感凭据、大块 Base64、完整工具定义和完整原始请求不会写入 `request_meta`。
5. 成功、失败、同步、异步和流式路径遵守同一日志契约。
6. 历史日志和现有日志查询、删除、详情功能保持兼容。
