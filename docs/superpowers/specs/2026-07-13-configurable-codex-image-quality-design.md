# Codex 图片生成质量配置设计

## 目标

在系统配置页面增加 `codex-gpt-image-2` 图片生成质量配置，并持久化到 `config.json`。管理员可以选择 `auto`、`low`、`medium` 或 `high`，无需修改每个客户端的请求参数。

## 配置语义

- 配置键：`codex_image_quality`
- 可选值：`auto`、`low`、`medium`、`high`
- 默认值：`auto`
- `auto`：保留调用方请求中的 `quality`；调用方未提供时仍使用现有默认值 `auto`
- `low`、`medium`、`high`：强制覆盖调用方请求中的 `quality`
- 非法配置值归一化为 `auto`

该配置只影响 `codex-gpt-image-2` 以及带套餐前缀的 Codex 图片模型别名，不影响普通 `gpt-image-2` 的 ChatGPT Web `picture_v2` 链路。

## 后端设计

`ConfigStore` 负责读取、归一化、公开和保存 `codex_image_quality`。Codex 图片请求在组装 `/backend-api/codex/responses` 的 `image_generation` 工具载荷时解析最终质量：配置为 `auto` 时采用请求值，否则采用配置值。

最终上游载荷继续使用现有字段：

```json
{
  "tools": [
    {
      "type": "image_generation",
      "model": "gpt-image-2",
      "quality": "high"
    }
  ]
}
```

## 前端设计

在现有系统配置卡片中、`Web 生图思考等级` 附近增加 `Codex 生图质量` 下拉框，选项为：

- 自动：遵循请求参数
- 低
- 中
- 高

说明文字明确该配置仅影响 `codex-gpt-image-2`，选择非自动值时会覆盖 API 请求中的 `quality`。保存继续复用现有 `/api/settings` 接口和保存按钮。

## 数据流

1. 配置页加载 `/api/settings`，缺少配置时展示 `auto`。
2. 管理员选择质量并保存，后端将规范值写入 `config.json`。
3. Codex 图片请求进入现有生成或编辑链路。
4. 组装 Codex `image_generation` 工具载荷时应用配置覆盖规则。
5. 日志中的最终请求调试信息继续记录实际发送的质量值。

## 测试范围

- `ConfigStore`：缺省、合法值、大小写与空白、非法值、持久化。
- Codex 上游载荷：`auto` 遵循请求值，固定等级覆盖请求值。
- 普通 Web 图片链路：不读取该配置，行为保持不变。
- 前端：配置归一化、状态更新、保存载荷和下拉选项。
- 完成后运行后端定向测试、前端测试和生产构建。

## 非目标

- 不修改普通 `gpt-image-2` 的 `image_thinking_effort`。
- 不改变单次 API 的质量参数定义。
- 不增加按账号或按 API Key 的图片质量策略。
- 不修改 Codex 外层响应模型、图片尺寸或输出格式。
