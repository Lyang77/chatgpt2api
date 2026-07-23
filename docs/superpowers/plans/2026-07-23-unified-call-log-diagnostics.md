# 统一调用日志诊断信息实施计划

> **给 agentic workers：** 必须使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 按任务逐步执行本计划。步骤使用 checkbox（`- [ ]`）语法跟踪。

**目标：** 为图片和文本调用日志增加安全的 `request_meta` 摘要，并在日志详情中展示请求参数和执行诊断。

**架构：** 后端新增无副作用的允许列表提取器，各 API 入口和图片异步任务共用它；`LoggedCall` 负责把摘要带入所有终态日志。前端新增纯格式化模块，把稳定字段映射为中文键值行，页面只负责渲染。

**技术栈：** Python 3、FastAPI、unittest、Next.js 16、React 19、TypeScript、Node.js `assert`。

## 全局约束

- 不记录 Authorization、API Key、Cookie、访问令牌、Base64、Data URL、完整 URL、完整消息、完整工具定义或原始请求 JSON。
- 不改变模型请求参数或生成语义，只增加日志投影和 UI 展示。
- 保留现有 `detail` 字段；历史日志没有 `request_meta` 时必须兼容。
- `request_meta` 仅由允许列表中的短标量、短字符串数组和计数字典组成。
- 不新增数据库列。

## 执行状态

- [x] 安全摘要提取器与通用 API 入口
- [x] 图片异步任务（含续轮询）及 PPT/PSD 终态日志
- [x] 日志详情“请求参数/执行诊断”界面
- [ ] 最终全量回归与工作区检查

---

### 任务 1：安全请求摘要提取器

**文件：**
- 新建：`services/request_log_meta.py`
- 新建：`test/test_request_log_meta.py`

**接口：**
- 产出：`build_image_request_meta(payload: dict[str, Any], *, mode: str, reference_image_count: int | None = None, mask_image_count: int | None = None) -> dict[str, Any]`
- 产出：`build_text_request_meta(payload: dict[str, Any], *, protocol: str) -> dict[str, Any]`

- [ ] **步骤 1：先写失败测试**

```python
class RequestLogMetaTests(unittest.TestCase):
    def test_image_meta_keeps_diagnostics_without_payload_content(self):
        payload = {
            "size": "1536x1024", "quality": "high", "n": 2,
            "output_format": "webp", "response_format": "url",
            "client_task_id": "task-1", "stream": True,
            "image": "data:image/png;base64,SECRET", "api_key": "SECRET",
        }
        self.assertEqual(build_image_request_meta(payload, mode="edit", reference_image_count=1, mask_image_count=1), {
            "mode": "edit", "size": "1536x1024", "quality": "high", "n": 2,
            "output_format": "webp", "response_format": "url", "client_task_id": "task-1",
            "stream": True, "reference_image_count": 1, "mask_image_count": 1,
        })

    def test_text_meta_counts_structure_and_omits_secrets(self):
        payload = {
            "messages": [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "world"}],
            "tools": [{"type": "function", "function": {"name": "secret_tool", "parameters": {"secret": "x"}}}],
            "tool_choice": {"type": "function", "name": "secret_tool"},
            "stream": True, "temperature": 0.4, "max_completion_tokens": 200,
            "authorization": "Bearer SECRET",
        }
        meta = build_text_request_meta(payload, protocol="chat_completions")
        self.assertEqual(meta["message_count"], 2)
        self.assertEqual(meta["role_counts"], {"user": 1, "assistant": 1})
        self.assertEqual(meta["tool_count"], 1)
        self.assertEqual(meta["tool_choice_type"], "function")
        self.assertNotIn("tools", meta)
        self.assertNotIn("authorization", meta)
        self.assertNotIn("secret_tool", repr(meta))
```

- [ ] **步骤 2：运行测试并确认按预期失败**

运行：`python -m unittest test.test_request_log_meta -v`

预期：FAIL，提示 `services.request_log_meta` 不存在。

- [ ] **步骤 3：实现最小提取器**

实现只读取以下允许字段：图片字段 `size/quality/n/output_format/response_format/client_task_id/stream`；文本字段 `stream/max_tokens/max_completion_tokens/max_output_tokens/temperature/top_p/store/modalities`。通过专用函数统计 `messages/input/tools`，通过 `_choice_type` 和 `_response_format_type` 只取对象的 `type`，通过文本递归计数器只返回字符数。

- [ ] **步骤 4：运行测试并确认通过**

运行：`python -m unittest test.test_request_log_meta -v`

预期：全部 PASS。

- [ ] **步骤 5：提交任务 1**

```powershell
git add -- services/request_log_meta.py test/test_request_log_meta.py
git commit -m "feat: add safe request log metadata"
```

### 任务 2：通用日志和所有 API 入口接入

**文件：**
- 修改：`services/log_service.py`
- 修改：`api/ai.py`
- 修改：`api/image_tasks.py`
- 修改：`test/test_log_response_text.py`
- 修改：`test/test_ai_log_images.py`

**接口：**
- 消费：任务 1 的两个 builder。
- 修改：`LoggedCall.request_meta: dict[str, Any] | None = None`。

- [ ] **步骤 1：先写失败测试**

在 `test/test_log_response_text.py` 新增断言 `LoggedCall(..., request_meta={"stream": True}).log(...)` 后完整详情包含相同 `request_meta`，列表详情也保留它。在 `test/test_ai_log_images.py` 断言图片接口、Chat Completions 和 Responses 构造的 `_CapturedCall.kwargs["request_meta"]` 包含各自安全字段。

- [ ] **步骤 2：运行测试并确认按预期失败**

```powershell
python -m unittest test.test_log_response_text -v
python -m unittest test.test_ai_log_images -v
```

预期：FAIL，日志或 `_CapturedCall` 中缺少 `request_meta`。

- [ ] **步骤 3：扩展 LoggedCall**

给 dataclass 增加 `request_meta`；`log()` 中仅当它为非空字典时执行 `detail["request_meta"] = dict(self.request_meta)`，保证同步、失败和流式终态共用。

- [ ] **步骤 4：接入文本入口**

Chat Completions、Responses、Messages、Search 使用 `build_text_request_meta`；PPT/PSD 使用包含 `client_task_id`、`prompt_chars`、`reference_image_count` 的文本摘要。不得把请求头传给 builder。

- [ ] **步骤 5：接入图片同步和任务入口**

图片生成/编辑在 `LoggedCall` 和 `image_task_log_template` 中使用同一个 `request_meta`；图片任务入口的内容审核失败日志也带摘要。`attach_image_task_log_template` 新增 `request_meta` 参数并复制字典。

- [ ] **步骤 6：运行定向测试并确认通过**

```powershell
python -m unittest test.test_request_log_meta test.test_log_response_text test.test_ai_log_images -v
```

预期：全部 PASS。

- [ ] **步骤 7：提交任务 2**

```powershell
git add -- services/log_service.py api/ai.py api/image_tasks.py test/test_log_response_text.py test/test_ai_log_images.py
git commit -m "feat: record request diagnostics for api calls"
```

### 任务 3：图片异步任务成功和失败日志一致性

**文件：**
- 修改：`services/image_task_service.py`
- 修改：`services/editable_file_task_service.py`
- 修改：`test/test_image_task_service.py`
- 新建：`test/test_editable_file_task_service.py`

**接口：**
- 消费：`build_image_request_meta`。
- 修改：`ImageTaskService._log_call(..., request_meta: dict[str, Any] | None = None)`。

- [ ] **步骤 1：先写失败测试**

Mock `services.image_task_service.log_service.add`，提交尺寸为 `1536x1024`、质量为 `high` 的成功和失败任务，等待终态后断言日志 `detail["request_meta"]` 同时包含 `mode/size/quality/n/output_format/response_format/client_task_id/reference_image_count/mask_image_count` 中该路径可得的字段，并且不含图片字节。

- [ ] **步骤 2：运行测试并确认按预期失败**

运行：`python -m unittest test.test_image_task_service -v`

预期：FAIL，最终日志缺少 `request_meta`。

- [ ] **步骤 3：实现成功、超时和失败分支传播**

在 `_run_task` 开始时用 payload 和 mode 生成一次摘要，传给所有 `_log_call`；`_finish_task_error` 重新从同一 payload 生成；`_log_call` 把非空摘要复制到 detail。续轮询日志从持久化 task 的 `mode/size/quality/id` 重建摘要。

- [ ] **步骤 4：运行图片任务和子任务日志测试**

```powershell
python -m unittest test.test_image_task_service test.test_image_subtask_logs -v
```

预期：全部 PASS。

- [ ] **步骤 5：提交任务 3**

```powershell
git add -- services/image_task_service.py test/test_image_task_service.py
git commit -m "feat: preserve image task request diagnostics"
```

### 任务 4：日志详情请求参数与执行诊断 UI

**文件：**
- 新建：`web/src/lib/log-diagnostics.ts`
- 新建：`web/test/log-diagnostics.test.ts`
- 修改：`web/src/app/logs/page.tsx`
- 修改：`web/test/log-page-contract.test.ts`

**接口：**
- 产出：`getRequestMetaRows(detail: Record<string, unknown> | undefined): LogDiagnosticRow[]`
- 产出：`getExecutionDiagnosticRows(detail: Record<string, unknown> | undefined): LogDiagnosticRow[]`
- 产出：`LogDiagnosticRow = { key: string; label: string; value: string; multiline?: boolean }`

- [ ] **步骤 1：先写失败的格式化测试**

```ts
deepEqual(getRequestMetaRows({ request_meta: { size: "1536x1024", stream: true, role_counts: { user: 2 } } }), [
  { key: "size", label: "尺寸", value: "1536x1024" },
  { key: "stream", label: "流式", value: "是" },
  { key: "role_counts", label: "消息角色", value: "user: 2" },
]);
deepEqual(getRequestMetaRows({}), []);
match(getExecutionDiagnosticRows({ requested_model: "gpt-image-2", effective_model: "codex-gpt-image-2", error: "upstream failed" })[2].value, /upstream failed/);
```

- [ ] **步骤 2：运行测试并确认按预期失败**

运行：`node --experimental-strip-types web/test/log-diagnostics.test.ts`

预期：FAIL，提示模块不存在。

- [ ] **步骤 3：实现稳定字段映射和格式化**

按设计文档顺序维护 `REQUEST_META_LABELS` 和 `EXECUTION_LABELS`；布尔值转“是/否”，数组逗号连接，`role_counts` 转 `role: count`，未知对象不展示。错误行设置 `multiline: true`。

- [ ] **步骤 4：先扩展页面契约测试**

断言日志页导入两个 helper，包含“请求参数”“执行诊断”文案，并继续保留单滚动条契约。

- [ ] **步骤 5：渲染两个诊断区域**

基础信息卡下按存在性渲染“执行诊断”；请求页签在请求文本上方按存在性渲染“请求参数”。普通值使用 `break-words`，错误值跨列显示并允许复制；历史日志返回空数组时不渲染。

- [ ] **步骤 6：运行 Web 定向测试**

```powershell
node --experimental-strip-types web/test/log-diagnostics.test.ts
node --experimental-strip-types web/test/log-page-contract.test.ts
```

预期：全部退出码为 0。

- [ ] **步骤 7：运行生产构建**

运行：`npm run build`（工作目录：`web`）。

预期：Next.js 构建成功。

- [ ] **步骤 8：提交任务 4**

```powershell
git add -- web/src/lib/log-diagnostics.ts web/test/log-diagnostics.test.ts web/src/app/logs/page.tsx web/test/log-page-contract.test.ts docs/superpowers/plans/2026-07-23-unified-call-log-diagnostics.md
git commit -m "feat: show call diagnostics in log details"
```

### 任务 5：最终回归验证

- [ ] **步骤 1：运行后端定向回归**

```powershell
python -m unittest test.test_request_log_meta test.test_log_response_text test.test_ai_log_images test.test_image_task_service test.test_image_subtask_logs test.test_logs_api test.test_log_store -v
```

- [ ] **步骤 2：运行全部可执行 Web 测试并记录既有失败**

逐个运行 `web/test/*.test.ts`；预期本次新增测试通过，既有 `log-detail-content.test.ts` 仍可能因无扩展名导入失败，必须如实报告。

- [ ] **步骤 3：运行 Web 生产构建**

运行：`npm run build`（工作目录：`web`），预期成功。

- [ ] **步骤 4：检查提交与工作区**

运行：`git status --short`、`git diff --check HEAD~4..HEAD`、`git log -5 --oneline`。预期工作区干净且没有空白错误。
