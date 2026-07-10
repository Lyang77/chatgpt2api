# 图片子任务日志、在途查询与本地停止实施计划

> **给 agentic workers：** 必须使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans` 按任务逐步执行本计划。步骤使用 checkbox（`- [ ]`）语法跟踪。

**目标：** 让每张实际生成的图片拥有一条可持续更新的日志，并能在账号管理和日志管理中查看、筛选和本地停止。

**架构：** `system_log` 是持久化观测源，每张图片在启动时创建一条 `running` 调用日志；同一个 API 请求的图片共享 `batch_id`。内存中的 `log_id -> threading.Event` 仅用于当前进程传递取消信号，账号池继续使用既有的内存并发计数。

**技术栈：** Python 3.13、FastAPI、SQLite、`threading.Event`、Next.js 16、React 19、TypeScript。

## 全局约束

- 一条日志对应一张图片子任务；`n=4` 产生四条日志和一个共享 `batch_id`。
- 每条日志保存完整空白规范化 prompt 和持久化后的入参图片 URL；不保存原始 token 或图片 base64。
- 终态只有 `success`、`failed`、`stopped`；仅 `running` 在账号在途详情显示且可停止。
- 停止是本地协作式取消：停止重试、轮询和结果交付，释放账号槽位，忽略迟到上游结果；不调用上游取消接口。
- 现有非图片调用保持一次调用一条日志；图片调用不再额外写入汇总日志。
- 查询完整 prompt、账号在途详情和停止接口一律管理员可用。

## 文件结构

- `services/log_store.py`：更新现有日志、按运行中图片任务筛选并同步索引列。
- `services/log_service.py`：图片子任务日志数据、创建/更新方法、取消注册表。
- `services/protocol/conversation.py`：为每张图片创建上下文、更新阶段、检查取消并保证释放槽位。
- `services/protocol/openai_v1_image_generations.py`、`services/protocol/openai_v1_image_edit.py`：把日志模板和批次传入 `ConversationRequest`。
- `api/ai.py`：在图片端点构造输入快照，禁止 `LoggedCall` 再写图片汇总日志。
- `api/system.py`、`api/accounts.py`：日志筛选/停止和账号在途详情 API。
- `web/src/lib/api.ts`、`web/src/app/logs/page.tsx`、`web/src/app/accounts/page.tsx`：客户端请求、筛选/停止、在途弹窗。
- `test/test_log_store.py`、`test/test_image_subtask_logs.py`、`test/test_logs_api.py`、`test/test_accounts_api.py`、`web/test/*.test.tsx`：覆盖持久化、协议、API 和页面。

### Task 1: 增加可更新的图片子任务日志

**Files:**
- Modify: `services/log_store.py`
- Modify: `services/log_service.py`
- Test: `test/test_log_store.py`

**Interfaces:**
- `LogService.create_call(detail: dict[str, Any], summary: str) -> dict[str, Any]`
- `LogService.update_call(log_id: str, *, summary: str | None = None, detail_patch: dict[str, Any] | None = None) -> dict[str, Any] | None`
- `LogService.list_running_image_subtasks(account_email: str = "") -> list[dict[str, Any]]`

- [ ] **Step 1: 写失败测试**

```python
created = service.create_call({"status": "running", "endpoint": "/v1/images/generations"}, "文生图 进行中")
updated = service.update_call(created["id"], detail_patch={"status": "stopped", "account_email": "a@example.test"})
assert updated["detail"]["status"] == "stopped"
assert service.list(status="stopped", account_email="a@example.test")["total"] == 1
```

- [ ] **Step 2: 运行失败测试**

Run: `python -m unittest test.test_log_store -v`

Expected: FAIL because the create/update/query methods do not exist.

- [ ] **Step 3: 写最小实现**

```python
def update(self, log_id: str, item: dict[str, Any]) -> dict[str, Any] | None:
    normalized = self._normalize_item(item)
    detail = normalized["detail"]
    with self._connect() as connection, connection:
        cursor = connection.execute(
            "UPDATE system_log SET log_time=?, log_type=?, summary=?, key_name=?, account_email=?, status=?, detail_json=? WHERE id=?",
            (normalized["time"], normalized["type"], normalized["summary"], str(detail.get("key_name") or ""), str(detail.get("account_email") or ""), str(detail.get("status") or ""), json.dumps(detail, ensure_ascii=False, separators=(",", ":")), log_id),
        )
    return self.get_by_id(log_id) if cursor.rowcount else None
```

`LogService.update_call` 读取旧记录、合并 patch、计算 `ended_at`/`duration_ms`，再调用 store update。运行中查询仅返回 `detail.status == "running"` 且 endpoint 以 `/v1/images/` 开头的记录。

- [ ] **Step 4: 验证通过并提交**

Run: `python -m unittest test.test_log_store -v`

Expected: PASS。

Commit: `git add services/log_store.py services/log_service.py test/test_log_store.py && git commit -m "feat: support mutable image task logs"`

### Task 2: 建立取消上下文并在协议层释放资源

**Files:**
- Modify: `services/log_service.py`
- Modify: `services/protocol/conversation.py`
- Test: `test/test_image_subtask_logs.py`

**Interfaces:**
- `ImageTaskLogContext(log_id: str, batch_id: str, image_index: int, image_total: int, cancel_event: Event)`
- `image_task_registry.request_stop(log_id: str) -> tuple[bool, dict[str, Any] | None]`
- `ConversationRequest.image_task_factory: Callable[[int, int], ImageTaskLogContext] | None`

- [ ] **Step 1: 写失败测试**

```python
context = make_context(cancelled=True)
with self.assertRaises(ImageGenerationStopped):
    _generate_single_image(make_request(context), 1, 1)
account_service.get_available_access_token.assert_not_called()
```

并覆盖：取号后停止释放槽位、重试等待被 Event 唤醒、重复停止不重复释放。

- [ ] **Step 2: 运行失败测试**

Run: `python -m unittest test.test_image_subtask_logs.ImageTaskCancellationTests -v`

Expected: FAIL because cancellation context and stopped error do not exist.

- [ ] **Step 3: 写最小实现**

```python
def raise_if_stopped(context: ImageTaskLogContext | None) -> None:
    if context and context.cancel_event.is_set():
        raise ImageGenerationStopped("image task stopped", log_id=context.log_id)
```

在取号前、每次重试前、retry wait、流/轮询回调后与最终结果交付前检查；停止路径更新该日志为 `stopped` 并在 `finally` 恰好释放一次槽位、注销 Event。

- [ ] **Step 4: 验证通过并提交**

Run: `python -m unittest test.test_image_subtask_logs test.test_account_image_capabilities test.test_account_model_allowlist -v`

Expected: PASS。

Commit: `git add services/log_service.py services/protocol/conversation.py test/test_image_subtask_logs.py && git commit -m "feat: add local image task cancellation"`

### Task 3: 每张图片创建、更新同一条日志

**Files:**
- Modify: `api/ai.py`
- Modify: `services/protocol/openai_v1_image_generations.py`
- Modify: `services/protocol/openai_v1_image_edit.py`
- Modify: `services/protocol/conversation.py`
- Test: `test/test_image_subtask_logs.py`

**Interfaces:**
- 图片 payload 新增 `image_task_log_template` 和 `image_task_batch_id`。

- [ ] **Step 1: 写失败测试**

```python
rows = log_service.list(type="call", page_size=10)["items"]
assert len(rows) == 2
assert {row["detail"]["image_index"] for row in rows} == {1, 2}
assert len({row["detail"]["batch_id"] for row in rows}) == 1
assert {row["detail"]["status"] for row in rows} == {"success"}
```

- [ ] **Step 2: 运行失败测试**

Run: `python -m unittest test.test_image_subtask_logs.ImageTaskLogLifecycleTests -v`

Expected: FAIL because the image protocol only produces one final aggregate log.

- [ ] **Step 3: 写最小实现**

```python
template = {"key_id": identity.get("id"), "key_name": identity.get("name"), "role": identity.get("role"), "endpoint": endpoint, "model": model, "request_text": prompt, "request_urls": request_urls}
payload["image_task_log_template"] = template
payload["image_task_batch_id"] = uuid4().hex
```

Worker 创建记录时写入序号、总数、`getting_account`；取号后写账号/`generating`；轮询写 `polling`；成功/失败/停止均更新同一 ID。图片端点为 `LoggedCall.run` 增加跳过最终汇总日志的明确选项。

- [ ] **Step 4: 验证通过并提交**

Run: `python -m unittest test.test_image_subtask_logs test.test_log_response_text test.test_ai_log_images test.test_v1_images_edits_api -v`

Expected: PASS。

Commit: `git add api/ai.py services/protocol/openai_v1_image_generations.py services/protocol/openai_v1_image_edit.py services/protocol/conversation.py services/log_service.py test/test_image_subtask_logs.py && git commit -m "feat: log each image generation subtask"`

### Task 4: 暴露管理员筛选、停止和账号在途 API

**Files:**
- Modify: `api/system.py`
- Modify: `api/accounts.py`
- Modify: `services/log_service.py`
- Test: `test/test_logs_api.py`
- Test: `test/test_accounts_api.py`

**Interfaces:**
- `POST /api/logs/{log_id}/stop` -> `{ "stopped": bool, "item": SystemLog }`
- `GET /api/accounts/inflight?access_token=<token>` -> `{ "items": SystemLog[] }`
- `GET /api/logs` 新增 `model`、`endpoint`、`batch_id` 条件。

- [ ] **Step 1: 写失败 API 测试**

```python
response = client.post("/api/logs/log-running/stop", headers=AUTH_HEADERS)
assert response.status_code == 200
assert response.json()["stopped"] is True

response = client.get("/api/accounts/inflight", headers=AUTH_HEADERS, params={"access_token": "token-a"})
assert response.status_code == 200
assert response.json()["items"] == [running_row]
```

- [ ] **Step 2: 运行失败测试**

Run: `python -m unittest test.test_logs_api test.test_accounts_api -v`

Expected: FAIL with missing endpoints/methods.

- [ ] **Step 3: 写最小 API 实现**

```python
@router.post("/api/logs/{log_id}/stop")
async def stop_log(log_id: str, authorization: str | None = Header(default=None)):
    require_admin(authorization)
    stopped, item = await run_in_threadpool(log_service.request_stop, log_id.strip())
    if item is None:
        raise HTTPException(status_code=404, detail={"error": "log not found"})
    return {"stopped": stopped, "item": item}
```

账号端点先通过 `account_service.get_account` 映射 token 到邮箱，再返回该邮箱的 running 图片日志，绝不回传 token。日志过滤的 total 必须与分页结果采用同一套 model/endpoint/batch_id 条件。

- [ ] **Step 4: 验证通过并提交**

Run: `python -m unittest test.test_logs_api test.test_accounts_api -v`

Expected: PASS。

Commit: `git add api/system.py api/accounts.py services/log_service.py test/test_logs_api.py test/test_accounts_api.py && git commit -m "feat: expose image task controls"`

### Task 5: 日志管理页增加进行中查询与停止

**Files:**
- Modify: `web/src/lib/api.ts`
- Modify: `web/src/app/logs/page.tsx`
- Test: `web/test/logs-page.test.tsx`

- [ ] **Step 1: 写失败组件测试**

```tsx
render(<LogsContent />);
await userEvent.click(await screen.findByRole("button", { name: "停止任务" }));
expect(mockStopSystemLog).toHaveBeenCalledWith(runningImageLog.id);
expect(mockFetchLogs).toHaveBeenCalledTimes(2);
```

并测试 `running`、`stopped` 状态文案及模型、接口、批次 ID 筛选参数。

- [ ] **Step 2: 运行失败测试**

Run: `npm --prefix web test -- logs-page.test.tsx`

Expected: FAIL because client API、状态标签和停止按钮不存在。

- [ ] **Step 3: 写最小 UI 实现**

```tsx
const canStop = item.detail?.status === "running" && String(item.detail?.endpoint || "").startsWith("/v1/images/");
{canStop ? <Button aria-label="停止任务" onClick={() => void handleStop(item.id)}>停止</Button> : null}
```

停止前确认、成功后 toast 并刷新当前页；停止提交期间禁用同一行。详情展示批次、图片序号/总数、阶段、重试次数和停止时间。

- [ ] **Step 4: 验证通过并提交**

Run: `npm --prefix web test -- logs-page.test.tsx`

Expected: PASS。

Run: `npm --prefix web run build`

Expected: exit code 0。

Commit: `git add web/src/lib/api.ts web/src/app/logs/page.tsx web/test/logs-page.test.tsx && git commit -m "feat: manage running image task logs"`

### Task 6: 账号管理页展示当前在途详情

**Files:**
- Modify: `web/src/lib/api.ts`
- Modify: `web/src/app/accounts/page.tsx`
- Test: `web/test/accounts-page.test.tsx`

- [ ] **Step 1: 写失败组件测试**

```tsx
render(<AccountsPage />);
await userEvent.click(await screen.findByRole("button", { name: "查看 1 个在途任务" }));
expect(await screen.findByText("prompt text")).toBeInTheDocument();
expect(mockFetchAccountInflight).toHaveBeenCalledWith(account.access_token);
```

- [ ] **Step 2: 运行失败测试**

Run: `npm --prefix web test -- accounts-page.test.tsx`

Expected: FAIL because count is plain text and no detail API exists.

- [ ] **Step 3: 写最小 UI 实现**

```tsx
<button aria-label={`查看 ${inflight} 个在途任务`} onClick={() => void openInflightDialog(account)} className="font-semibold text-amber-600">{inflight}</button>
```

弹窗显示模型、图序号/总数、已耗时、阶段、重试次数、完整 prompt 和输入图片；打开后立即加载，只在打开期间每两秒刷新，空数组时停止刷新。

- [ ] **Step 4: 验证通过并提交**

Run: `npm --prefix web test -- accounts-page.test.tsx`

Expected: PASS。

Run: `npm --prefix web run build`

Expected: exit code 0。

Commit: `git add web/src/lib/api.ts web/src/app/accounts/page.tsx web/test/accounts-page.test.tsx && git commit -m "feat: show account image task details"`

### Task 7: 完整回归与交付

**Files:**
- Test: `test/test_image_subtask_logs.py`

- [ ] **Step 1: 补充迟到上游结果回归测试**

```python
outputs = list(stream_image_outputs_with_pool(request_that_stops_before_fake_result()))
assert outputs == []
assert log_service.get_by_id(log_id)["detail"]["status"] == "stopped"
```

- [ ] **Step 2: 验证完整后端和前端**

Run: `python -m unittest discover -s test -p "test_*.py" -v`

Expected: all tests PASS。

Run: `npm --prefix web run build`

Expected: exit code 0。

- [ ] **Step 3: 最终检查与提交**

Run: `git diff --check`

Expected: no output, exit code 0。

Commit: `git add services/log_store.py services/log_service.py services/protocol/conversation.py services/protocol/openai_v1_image_generations.py services/protocol/openai_v1_image_edit.py api/ai.py api/system.py api/accounts.py web/src/lib/api.ts web/src/app/logs/page.tsx web/src/app/accounts/page.tsx test/test_log_store.py test/test_image_subtask_logs.py test/test_logs_api.py test/test_accounts_api.py web/test/logs-page.test.tsx web/test/accounts-page.test.tsx && git commit -m "feat: add observable image subtask control"`

## 自检结果

- 一日志一子任务、批次关联、prompt/图片保存、进行中/已停止、筛选、账号详情、本地停止、管理员权限、异常释放和迟到结果丢弃均有对应任务。
- `ImageTaskLogContext`、`batch_id`、`running`、`stopped`、`POST /api/logs/{log_id}/stop` 和 `GET /api/accounts/inflight` 在各任务中命名一致。
- 计划没有 `TBD`、`TODO` 或未说明验证命令的实现步骤。

## 执行交接

计划已保存到 `docs/superpowers/plans/2026-07-10-image-subtask-logs-plan.md`。

1. **子代理驱动（推荐）**：每个任务安排新的子代理，任务间复核。
2. **当前会话直接执行**：在当前会话按计划分批执行并设置复核检查点。
