# 单任务单请求流式多图实施计划

> **给 agentic workers：** 必须使用 `superpowers:executing-plans` 按任务逐步执行本计划。步骤使用 checkbox（`- [ ]`）跟踪。

**目标：** 图片任务使用一个线程和一次上游流式请求收集任意数量图片，依据上游终态或超时结束，将全部结果增量保存到同一任务的 `data[]`，并在页面与日志中完整展示。

**架构：** `/api/image-tasks/*` 不传递 `n`，而是通过内部结果回调把一次上游流中发现的新图片追加到任务。普通 ChatGPT 图片链路增加任务专用的严格终态轮询，Codex 链路逐事件累计 `image_generation_call`；OpenAI 兼容 `/v1/images/*` 的现有 `n` 并发语义保持不变。

**技术栈：** Python 3、FastAPI、`unittest`、React 19、Next.js 16、TypeScript。

## 全局约束

- 不修改用户当前在 `api/ai.py`、`api/image_inputs.py`、`test/test_v1_images_edits_json.py` 中的未提交改动，除非实现确实需要且能无冲突保留。
- 不执行 `git add`、`git commit`、`git reset`、`git checkout` 等 Git 写操作。
- 一个任务只启动一个后台线程，只调用一次上游图片请求。
- 任务链路不增加、不解析、不记录 `n` 或期望图片数量。
- 有至少一张图片时，即使上游部分完成或超时也标记 `success`；没有图片才标记 `error`。

---

### 任务 1：识别普通图片上游终态并支持严格轮询

**文件：**
- 修改：`services/openai_backend_api.py`
- 测试：`test/test_multi_image_results.py`

**接口：**
- 产出：`OpenAIBackendAPI._image_generation_terminal_state(data) -> str`，返回 `"success"`、`"failed"` 或 `""`。
- 产出：`_poll_image_results(..., wait_for_terminal=False, result_ids_callback=None)`；默认行为兼容旧接口，任务链路可等待明确终态。

- [ ] **步骤 1：编写终态与严格轮询失败测试**

在测试会话消息中加入 `status`、`end_turn`，新增测试证明：第一批文件 ID 稳定但状态仍为 `in_progress` 时不能返回；状态变为 `finished_successfully` 后返回全部 ID。另测 `metadata.is_error=true` 返回失败终态。

```python
def test_strict_poll_waits_for_image_terminal_state(self) -> None:
    backend = FakeBackend([
        _conversation(["file-one"], status="in_progress"),
        _conversation(["file-one"], status="in_progress"),
        _conversation(["file-one", "file-two"], status="finished_successfully", end_turn=True),
    ])
    snapshots = []
    file_ids, _ = backend._poll_image_results(
        "conv-1",
        timeout_secs=10,
        wait_for_terminal=True,
        result_ids_callback=lambda files, sediments: snapshots.append((list(files), list(sediments))),
    )
    self.assertEqual(file_ids, ["file-one", "file-two"])
    self.assertEqual(snapshots[-1][0], ["file-one", "file-two"])
```

- [ ] **步骤 2：运行测试并确认按预期失败**

运行：`python -m unittest test.test_multi_image_results`

预期：因 `_conversation()` 不接受状态参数，或 `_poll_image_results()` 不接受 `wait_for_terminal` 而失败。

- [ ] **步骤 3：实现最小终态提取与严格轮询**

遍历图片工具消息，按消息 `status`、`end_turn` 和 `metadata.is_error` 判定终态。严格模式发现新 ID 时调用 `result_ids_callback`，只有终态或超时才结束；非严格模式保留现有“ID 稳定”逻辑。

```python
def _image_generation_terminal_state(self, data: Dict[str, Any]) -> str:
    success_statuses = {"finished_successfully", "finished_partial_completion", "completed"}
    failed_statuses = {"failed", "cancelled", "expired"}
    state = ""
    for node in (data.get("mapping") or {}).values():
        message = (node or {}).get("message") or {}
        metadata = message.get("metadata") or {}
        if metadata.get("async_task_type") != "image_gen" and not self._has_image_asset_pointer(message):
            continue
        status = str(message.get("status") or "").strip().lower()
        if metadata.get("is_error") is True or status in failed_statuses:
            return "failed"
        if status in success_statuses or message.get("end_turn") is True:
            state = "success"
    return state
```

- [ ] **步骤 4：运行测试确认通过**

运行：`python -m unittest test.test_multi_image_results`

预期：全部通过，旧的非严格稳定性测试仍保持 3 次查询。

---

### 任务 2：一次流式请求累计并回调全部图片结果

**文件：**
- 修改：`services/protocol/conversation.py`
- 修改：`services/protocol/openai_v1_image_generations.py`
- 修改：`services/protocol/openai_v1_image_edit.py`
- 修改：`services/openai_backend_api.py`
- 测试：`test/test_multi_image_results.py`
- 测试：`test/test_codex_image_output_format.py`

**接口：**
- 产出：`ConversationRequest.image_result_callback: Callable[[list[dict[str, Any]]], None] | None`。
- 产出：`ConversationRequest.wait_for_image_terminal: bool`。
- 约定：协议最终返回仍为标准 `{"data": [...]}`，内部回调只传新出现且已格式化的结果。

- [ ] **步骤 1：编写普通链路和 Codex 链路失败测试**

普通链路测试严格轮询回调的多批 ID 最终只产生一次请求并向结果回调追加去重图片；Codex 测试用两个 `image_generation_call` 事件和 `response.completed` 证明回调收到两张，最终 `data` 也有两张。

```python
received = []
request = ConversationRequest(
    model="codex-gpt-image-2",
    prompt="draw variants",
    image_result_callback=lambda items: received.extend(items),
    wait_for_image_terminal=True,
)
outputs = list(stream_codex_image_outputs(backend, request))
self.assertEqual(len(received), 2)
self.assertEqual(len(outputs[0].data), 2)
```

- [ ] **步骤 2：运行测试并确认按预期失败**

运行：`python -m unittest test.test_multi_image_results test.test_codex_image_output_format`

预期：`ConversationRequest` 缺少新字段或回调未触发。

- [ ] **步骤 3：实现结果回调和终态透传**

`openai_v1_image_generations.handle()` 与 `openai_v1_image_edit.handle()` 从内部 body 读取回调和严格终态标记并写入 `ConversationRequest`。普通链路在每批新 URL 完成下载、格式化后回调；Codex 不再 `list(...)` 后统一解析，而是逐事件提取未见过的结果、调用回调，最后返回累计数据。

Codex 指令改为：

```python
CODEX_RESPONSES_INSTRUCTIONS = (
    "Use the image_generation tool to fulfill the user's image request. "
    "Return every generated image result."
)
```

任务严格模式传给 `_poll_image_results(wait_for_terminal=True)`；普通 `/v1/images/*` 请求默认仍为 `False`。

严格任务模式的所有轮询预算读取 `config.image_poll_timeout_secs`。轮询超时、文本回复或连接异常直接结束本次任务，不进入账号切换或第二次上游请求；兼容接口原有重试语义保持不变。

- [ ] **步骤 4：运行测试确认通过**

运行：`python -m unittest test.test_multi_image_results test.test_codex_image_output_format test.test_codex_4k`

预期：全部通过，Codex 的尺寸、格式和旧单图行为不回归。

---

### 任务 3：任务运行中增量保存多图并记录实际数量

**文件：**
- 修改：`services/image_task_service.py`
- 修改：`services/log_service.py`
- 测试：`test/test_image_task_service.py`
- 测试：`test/test_image_subtask_logs.py`

**接口：**
- 产出：公开任务字段 `actual_image_count: int`、`completion_reason: str`。
- 产出：内部 `_append_task_data(key, items) -> list[dict[str, Any]]`，按 URL、`b64_json` 内容去重。

- [ ] **步骤 1：编写增量持久化和完成原因失败测试**

测试 handler 调用 `payload["image_result_callback"]` 两次，在 handler 阻塞期间轮询可见部分 `data`；重复结果不重复；最终任务成功并保存 `actual_image_count=2`。另测 handler 最终抛出超时但已回调一张时仍成功且 `completion_reason="timeout_with_results"`。

```python
def handler(payload):
    payload["image_result_callback"]([{"url": "http://example.test/one.png"}])
    released.wait(1)
    payload["image_result_callback"]([
        {"url": "http://example.test/one.png"},
        {"url": "http://example.test/two.png"},
    ])
    return {"data": [{"url": "http://example.test/one.png"}, {"url": "http://example.test/two.png"}]}
```

- [ ] **步骤 2：运行测试并确认按预期失败**

运行：`python -m unittest test.test_image_task_service test.test_image_subtask_logs`

预期：payload 没有 `image_result_callback`，公开任务也没有数量字段。

- [ ] **步骤 3：实现任务增量结果、持久化与日志字段**

提交 payload 不再显式包含 `"n": 1`，改为内部标志 `"wait_for_image_terminal": True`。`_run_task()` 注入结果回调，回调使用 `_append_task_data()` 更新 `data` 和按 `len(data)` 计算的 `actual_image_count`。最终结果与增量结果合并去重。

成功日志写入：

```python
detail["actual_image_count"] = actual_image_count
detail["completion_reason"] = completion_reason
detail["response_image_urls"] = list(dict.fromkeys(urls or []))
```

旧任务加载时按 `len(data)` 补齐数量；公开响应携带新增字段。超时异常且任务已有数据时转为成功，其他异常仍失败。

- [ ] **步骤 4：运行测试确认通过**

运行：`python -m unittest test.test_image_task_service test.test_image_subtask_logs test.test_image_tasks_api`

预期：全部通过。

---

### 任务 4：Web 生图页用一个任务展示多个结果

**文件：**
- 修改：`web/src/lib/api.ts`
- 修改：`web/src/store/image-conversations.ts`
- 修改：`web/src/app/image/page.tsx`
- 修改：`web/src/app/image/components/image-composer.tsx`
- 新建：`web/src/lib/image-task-results.ts`
- 新建：`web/test/image-task-results.test.ts`

**接口：**
- 产出：`mergeImageTaskResults(images: StoredImage[], task: ImageTask): StoredImage[]`。
- 约定：结果 ID 使用 `task.id` 加 URL 或 `b64_json` 摘要；运行中保留一个 loading 哨兵，完成后移除。

- [ ] **步骤 1：为任务结果展开编写失败测试**

```typescript
const merged = mergeImageTaskResults([{ id: "slot", taskId: "task-1", status: "loading" }], {
  id: "task-1",
  status: "running",
  mode: "generate",
  created_at: "2026-07-13 00:00:00",
  updated_at: "2026-07-13 00:00:01",
  data: [{ url: "/one.png" }, { url: "/two.png" }],
});
equal(merged.filter((item) => item.status === "success").length, 2);
equal(merged.filter((item) => item.status === "loading").length, 1);
```

- [ ] **步骤 2：运行测试并确认按预期失败**

运行：`node --experimental-strip-types web/test/image-task-results.test.ts`

预期：模块不存在。

- [ ] **步骤 3：实现纯函数并接入页面**

实现稳定去重和 loading 哨兵规则。页面同步任务时使用该函数替换 `taskDataToStoredImage()` 的单结果逻辑。每次提交只创建一个 loading 图片和一个任务；重试同样只创建一个任务。

从 `ImageComposerProps`、页面 state、localStorage 和尺寸菜单中移除 `imageCount`、`onImageCountChange`、生成数量选择器；尺寸标签只显示质量和比例。`ImageTask` 类型增加 `actual_image_count`、`completion_reason`。

- [ ] **步骤 4：运行前端测试和类型检查**

运行：

```powershell
node --experimental-strip-types web/test/image-task-results.test.ts
npm exec tsc -- --noEmit
```

工作目录：`web`

预期：纯函数测试输出成功，TypeScript 无错误。

---

### 任务 5：日志详情直观显示实际图片数量

**文件：**
- 修改：`web/src/app/logs/page.tsx`
- 新建：`web/src/lib/image-log-summary.ts`
- 新建：`web/test/image-log-summary.test.ts`

**接口：**
- 产出：`getImageLogSummary(detail) -> { actualCount: number; completionReason: string } | null`。

- [ ] **步骤 1：编写日志摘要失败测试**

```typescript
deepEqual(getImageLogSummary({ actual_image_count: 3, completion_reason: "upstream_completed" }), {
  actualCount: 3,
  completionReason: "upstream_completed",
});
```

- [ ] **步骤 2：运行测试并确认按预期失败**

运行：`node --experimental-strip-types web/test/image-log-summary.test.ts`

预期：模块不存在。

- [ ] **步骤 3：实现摘要并接入日志结果页**

结果页图片画廊上方展示卡片“实际返回数量：N”。`completion_reason="timeout_with_results"` 时额外展示“上游等待超时，已保留现有结果”；其他成功原因不显示警告。无图片任务字段时不展示卡片。

- [ ] **步骤 4：运行测试和构建**

工作目录：`web`

运行：

```powershell
node --experimental-strip-types test/image-log-summary.test.ts
npm exec tsc -- --noEmit
npm run build
```

预期：测试、类型检查和生产构建全部通过。

---

### 任务 6：全量回归与工作区审计

**文件：**
- 检查：本计划涉及的全部文件
- 检查：用户原有未提交改动

- [ ] **步骤 1：运行后端定向回归**

运行：

```powershell
python -m unittest test.test_multi_image_results test.test_codex_image_output_format test.test_codex_4k test.test_image_task_service test.test_image_subtask_logs test.test_image_tasks_api
```

预期：全部通过。

- [ ] **步骤 2：运行相关图片 API 回归**

运行：

```powershell
python -m unittest test.test_v1_images_edits_json test.test_v1_images_edits_api test.test_v1_responses
```

预期：全部通过，OpenAI 兼容 `n` 行为未改变。

- [ ] **步骤 3：检查格式与差异边界**

运行：

```powershell
git diff --check
git status --short
```

预期：无空白错误；原有三个修改文件仍被保留，新增差异仅属于本功能和设计/计划文档。
