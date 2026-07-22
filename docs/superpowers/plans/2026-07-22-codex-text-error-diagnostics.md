# Codex 文本错误诊断实施计划

> **给 agentic workers：** 必须使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 按任务逐步执行本计划。步骤使用 checkbox（`- [ ]`）语法跟踪。

**目标：** 在不向客户端泄露上游错误内容的前提下，把 Codex 文本失败终止事件的结构化诊断信息持久化到调用日志。

**架构：** Codex 协议层使用专用异常分离公开错误字符串与 `diagnostic_detail`；通用调用日志层只识别这一内部诊断属性并保存为 `detail.upstream_error`。诊断字段采用允许列表、现有 Codex 清洗函数和长度限制，正常响应与重试逻辑保持不变。

**技术栈：** Python 3、FastAPI、`unittest`、现有 `LogService`/`LoggedCall` 与 Codex SSE 解析器。

## 全局约束

- 客户端仍只收到 `Codex text generation failed: <event_type>`。
- 只保存 `event_type`、`type`、`code`、`message`、`response_id`、`incomplete_reason`。
- 所有上游字符串先经过 Codex 日志清洗并截断；不得保存完整事件、令牌、Authorization、data URL、图片 base64、请求体或推理内容。
- 不改变账户选择、无输出换号重试、模型路由或成功响应。
- 不补写历史日志，不增加前端组件。

---

### 任务 1：构造安全的 Codex 文本诊断异常

**文件：**
- 修改：`services/protocol/codex_text.py:13-177`
- 测试：`test/test_codex_text_transport.py:262-406`

**接口：**
- 产生：`CodexTextGenerationError(event_type: str, diagnostic_detail: dict[str, object])`
- 产生：`_codex_failure_diagnostic(event: dict[str, Any]) -> dict[str, object]`
- 消费：现有 `_codex_text_event_deltas(events)`

- [ ] **步骤 1：编写失败测试**

新增测试，断言顶层 `error`、嵌套 `response.error`、`response.incomplete_details` 和失败状态 `response.completed` 均生成结构化诊断；同时断言 `str(exc)` 不含上游消息、Bearer token 或 data URL。核心断言：

```python
with self.assertRaises(codex_text.CodexTextGenerationError) as raised:
    list(codex_text._codex_text_event_deltas(iter([event])))

self.assertEqual(raised.exception.diagnostic_detail, {
    "event_type": "error",
    "type": "upstream_error",
    "code": "rate_limit_exceeded",
    "message": "request failed: Bearer [redacted] data:image/[redacted];base64,[redacted]",
    "response_id": "resp_test",
})
self.assertEqual(str(raised.exception), "Codex text generation failed: error")
```

- [ ] **步骤 2：验证测试按预期失败**

运行：

```powershell
python -m unittest test.test_codex_text_transport.CodexTextTransportTests -v
```

预期：新测试因 `CodexTextGenerationError` 或 `diagnostic_detail` 尚不存在而失败；已有测试继续通过到该失败点。

- [ ] **步骤 3：实现最小异常与提取逻辑**

在 `services/protocol/codex_text.py` 中新增专用异常和允许列表提取函数。字符串清洗调用现有 `OpenAIBackendAPI._codex_log_value(value, limit)`，只接受清洗后的标量：

```python
class CodexTextGenerationError(RuntimeError):
    def __init__(self, event_type: str, diagnostic_detail: dict[str, object]) -> None:
        super().__init__(f"Codex text generation failed: {event_type}")
        self.diagnostic_detail = diagnostic_detail


def _codex_failure_diagnostic(event: dict[str, Any]) -> dict[str, object]:
    event_type = str(event.get("type") or "")
    response = event.get("response") if isinstance(event.get("response"), dict) else {}
    error = event.get("error") if isinstance(event.get("error"), dict) else response.get("error")
    error = error if isinstance(error, dict) else {}
    incomplete = (
        event.get("incomplete_details")
        if isinstance(event.get("incomplete_details"), dict)
        else response.get("incomplete_details")
    )
    incomplete = incomplete if isinstance(incomplete, dict) else {}
    candidates = {
        "event_type": event_type,
        "type": error.get("type"),
        "code": error.get("code"),
        "message": error.get("message"),
        "response_id": event.get("response_id") or response.get("id") or event.get("id"),
        "incomplete_reason": incomplete.get("reason"),
    }
    return _clean_codex_diagnostic(candidates)
```

把三类终止事件和失败状态 `response.completed` 的普通 `RuntimeError` 替换成该专用异常，不改变成功路径。

- [ ] **步骤 4：验证任务 1 通过**

运行：

```powershell
python -m unittest test.test_codex_text_transport -v
```

预期：全部通过；现有“异常字符串不含敏感上游内容”测试保持通过。

- [ ] **步骤 5：提交任务 1**

```powershell
git add -- services/protocol/codex_text.py test/test_codex_text_transport.py
git commit -m "fix: preserve codex text failure diagnostics"
```

### 任务 2：把内部诊断持久化到调用日志

**文件：**
- 修改：`services/log_service.py:770-940`
- 测试：`test/test_log_response_text.py:17-161`

**接口：**
- 消费：异常的 `diagnostic_detail: dict[str, object]`
- 扩展：`LoggedCall.log` 增加可选参数 `diagnostic_detail: dict[str, object] | None = None`
- 产生：调用日志 `detail.upstream_error`

- [ ] **步骤 1：编写失败测试**

新增一个同步 `LoggedCall.run` 失败测试和一个流式 `LoggedCall.stream` 失败测试。使用仅含内部诊断属性的测试异常，断言客户端错误正文/抛出的异常字符串仍为通用错误，而日志详情包含完全相同的 `upstream_error`：

```python
class DiagnosticError(RuntimeError):
    diagnostic_detail = {"event_type": "error", "code": "rate_limit_exceeded"}

response = asyncio.run(call.run(lambda: (_ for _ in ()).throw(DiagnosticError("safe error"))))
detail = self._last_detail()
self.assertEqual(detail["error"], "safe error")
self.assertEqual(detail["upstream_error"], DiagnosticError.diagnostic_detail)
```

另加普通异常回归断言：没有 `diagnostic_detail` 时不写 `upstream_error`。

- [ ] **步骤 2：验证测试按预期失败**

运行：

```powershell
python -m unittest test.test_log_response_text.LoggedCallResponseTextTests -v
```

预期：新诊断日志断言失败，因为当前 `LoggedCall` 只保存 `str(exc)`。

- [ ] **步骤 3：实现最小日志透传**

新增只接受非空字典的内部提取函数，并在 `LoggedCall.run` 的同步处理、首个流项目读取，以及 `LoggedCall.stream` 的迭代失败路径传给 `log()`：

```python
def _exception_diagnostic_detail(exc: Exception) -> dict[str, object] | None:
    detail = getattr(exc, "diagnostic_detail", None)
    return dict(detail) if isinstance(detail, dict) and detail else None
```

扩展 `LoggedCall.log()`：

```python
if diagnostic_detail:
    detail["upstream_error"] = dict(diagnostic_detail)
```

不把 `upstream_error` 放入客户端响应，不改变结构化 HTTP 错误处理。

- [ ] **步骤 4：验证任务 2 通过**

运行：

```powershell
python -m unittest test.test_log_response_text -v
```

预期：全部通过，诊断字段只存在于日志详情。

- [ ] **步骤 5：运行聚焦回归**

运行：

```powershell
python -m unittest test.test_codex_text_transport test.test_log_response_text test.test_ai_log_images -v
python -m ruff check services/protocol/codex_text.py services/log_service.py test/test_codex_text_transport.py test/test_log_response_text.py
```

预期：所有测试通过，Ruff 无错误。

- [ ] **步骤 6：检查差异并提交任务 2**

```powershell
git diff --check
git diff -- services/log_service.py test/test_log_response_text.py
git add -- services/log_service.py test/test_log_response_text.py docs/superpowers/plans/2026-07-22-codex-text-error-diagnostics.md
git commit -m "fix: record upstream diagnostics in call logs"
```
