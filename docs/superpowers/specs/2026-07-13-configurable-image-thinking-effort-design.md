# 可配置的 Web 生图思考等级设计

## 目标

为普通 `gpt-image-2` Web 模拟链路增加全局思考等级配置。管理员可在前端配置页选择等级，保存后写入 `config.json`，后续 `gpt-image-2 -> gpt-5-3 + picture_v2` 请求使用该配置。

## 配置契约

- 配置键：`image_thinking_effort`
- 默认值：`high`
- 有效值：空字符串、`low`、`medium`、`high`、`extended`
- 前端显示：关闭、低、中、高、扩展
- 非法配置值：后端归一化为 `high`
- “关闭”保存为空字符串；请求载荷中不发送 `thinking_effort`

## 生效范围

仅影响普通 `gpt-image-2` 的 ChatGPT Web 图片链路：

1. `POST /backend-api/f/conversation/prepare`
2. `POST /backend-api/f/conversation`

两个请求使用相同的配置值。以下链路不受影响：

- 普通文本模型请求
- `codex-gpt-image-2` 及其 Plus、Team、Pro 别名
- 外部 `/v1/images/generations` 和 `/v1/images/edits` 的请求参数契约

## 后端设计

`services/config.py` 提供归一化后的 `image_thinking_effort` 属性，并在 `get()` 返回给配置 API。图片 prepare 和正式请求构造器读取该属性：值非空时加入顶层 `thinking_effort`，为空时省略字段。

配置保存后，后续新建的图片请求直接读取当前配置，无需为调用方增加请求参数。

## 前端设计

在“系统配置”的图片设置区域增加“Web 生图思考等级”下拉框，靠近图片轮询超时和单账号图片并发配置。

下拉项：

- 关闭：空字符串
- 低：`low`
- 中：`medium`
- 高：`high`
- 扩展：`extended`

说明文字明确：该配置只影响 `gpt-image-2` 的 `gpt-5-3 + picture_v2` Web 链路，不影响 Codex 图片模型。保存继续复用现有配置保存按钮和 API。

## 错误与兼容处理

- 老 `config.json` 没有该字段时按 `high` 处理。
- 配置为未知值时按 `high` 处理，避免向上游发送任意字符串。
- 配置为关闭时完全省略 `thinking_effort`，保持当前请求形态，作为上游兼容回退手段。
- 不根据单次请求覆盖全局配置，避免同一账号池出现不可审计的混合行为。

## 验证

后端测试覆盖：

1. 缺少配置时默认为 `high`。
2. 四个有效等级可正确读取。
3. 非法值回退为 `high`。
4. 关闭时 prepare 和正式请求均不含 `thinking_effort`。
5. 启用时 prepare 和正式请求均携带相同等级。
6. Codex 图片链路不读取该配置。

前端测试覆盖配置归一化、下拉选项和保存载荷。完成静态测试后，使用真实 `gpt-image-2` 请求验证上游接受 `high` 且仍能返回图片；再切换为关闭验证回退路径。

## 非目标

- 不增加请求级 `thinking_effort` 入参。
- 不修改 `gpt-image-2 -> gpt-5-3` 模型映射。
- 不修改图片质量、尺寸、格式或轮询策略。
- 不承诺思考等级一定改善图片质量；它只是传递给 ChatGPT Web 上游的控制字段。
