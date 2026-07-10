# 日志详情结果视图实施计划

> **给 agentic workers：** 必须使用 `superpowers:subagent-driven-development` 或 `superpowers:executing-plans` 按任务逐步执行本计划。步骤使用 checkbox（`- [ ]`）跟踪。

**目标：** 将日志详情页改为默认阅读最终文本和图片结果，同时保留请求与完整原始数据供排查。

**架构：** `LoggedCall` 记录明确的请求图片和返回图片稳定 URL；`logs/page.tsx` 在固定字段概览外以页签展示结果、请求与原始数据。图片继续使用已存在的缩略图与大图预览组件。

**技术栈：** Next.js、React、TypeScript、Tailwind CSS、lucide-react。

## 全局约束

- 不改变日志 API 路径、SQLite 表或既有 `detail.urls`；新增兼容字段 `request_urls` 和 `response_image_urls`。
- `response_text` 仅用于结果展示，完整值仍可在原始数据中查看。
- 不向客户端响应或 UI 暴露内部账号追踪字段。

---

### 任务 1：记录请求与返回图片

**文件：**
- 修改：`api/ai.py`
- 修改：`services/log_service.py`
- 修改：`test/test_log_response_text.py`

**接口：**
- 输入：聊天/Responses 请求 payload、非流式响应或流式响应结果
- 输出：`detail.request_urls`、`detail.response_image_urls`

- [ ] 编写失败测试：data URL 请求图片保存为稳定 `request_urls`，`data[].b64_json` 与 Responses 图片结果保存为 `response_image_urls`。
- [ ] 聊天与 Responses 路由创建 `LoggedCall` 时传入 `collect_request_image_urls(payload, resolve_image_base_url(request))`。
- [ ] `LoggedCall` 仅从明确的图片结果字段收集返回图片，保留既有 `urls` 兼容字段但不将其作为新图片视图的数据源。
- [ ] 运行 `python -m unittest test.test_log_response_text test.test_logs_api -v`。

### 任务 2：重构详情视图

**文件：**
- 修改：`web/src/app/logs/page.tsx`

- [ ] 使用固定字段定义渲染概览网格，并排除所有长内容字段。
- [ ] 增加“结果内容 / 请求内容 / 原始数据”页签，打开详情时默认选择结果内容。
- [ ] 在结果页显示最终文本和 `response_image_urls`，在请求页显示 Prompt 和 `request_urls`，在原始数据页显示完整 JSON。
- [ ] 复用 `ImageThumbnail` 和 `ImageLightbox`；图片为空时不渲染图片区块。

### 任务 3：验证

**文件：**
- 修改：`web/src/app/logs/page.tsx`

- [ ] 运行 `npm run build`（目录：`web`）。
- [ ] 启动或复用本地开发服务，以桌面和窄视口截图检查：概览无长文本、结果页只含最终结果、图片可见且可打开、原始数据可查看。
- [ ] 检查 `git diff --check`，确认仅包含详情页与本次设计/计划文档改动。
