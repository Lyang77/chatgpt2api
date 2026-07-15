# Codex GPT-5.5 文本模型支持实施计划

> **给 agentic workers：** 必须使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 按任务逐步执行本计划。步骤使用 checkbox（`- [ ]`）语法跟踪。

**目标：** 在 `/v1/chat/completions` 与 `/v1/responses` 中增加外部模型 `gpt-5.5`，强制使用 Codex 账户调用 `/backend-api/codex/responses`，支持同步、流式、文本和多图理解，并只输出最终文本。

**架构：** 新建聚焦的 `services/protocol/codex_text.py`，负责 Codex 多模态输入规范化、账户选择、SSE 文本提取和无输出重试边界。现有两个 OpenAI 协议模块只负责各自的输入/输出适配；底层 `OpenAIBackendAPI` 新增独立 Codex 文本传输方法，不改动 Codex 生图构造器。

**技术栈：** Python 3、FastAPI、`urllib.request`、SSE、`unittest`、现有 OpenAI 兼容协议工具。

## 全局约束

- 外部模型名必须精确为 `gpt-5.5`；现有 Web 模型 `gpt-5-5` 行为不变。
- `gpt-5.5` 只能选择 `source_type=codex` 账户，禁止回退 Web 账户。
- 同时支持 `/v1/chat/completions` 与 `/v1/responses` 的 `stream=false/true`。
- 支持 HTTP(S) URL、`data:image/...` URL 和多图顺序；不支持 multipart、本地路径或 `file_id`。
- 只输出最终文本，不暴露 reasoning、工具事件或上游原始事件。
- 非空 `tools` / `tool_choice` 返回 HTTP 400。
- 不改动现有 `codex-gpt-image-2` 请求行为。
- 严格 TDD：每项生产代码之前先运行对应失败测试，并确认失败原因是功能尚未实现。
- 保留工作区已有修改，不执行 `git add`、`commit`、`push` 或创建 PR。

---

## 文件结构

- 新建 `services/protocol/codex_text.py`：模型识别、多模态输入规范化、Codex 文本请求、账户强制路由和文本增量收集。
- 修改 `utils/helper.py`：公开 `CODEX_TEXT_MODEL` 与 `is_codex_text_model()`。
- 修改 `services/account_service.py`：文本账户选择增加可选 `source_type` 精确过滤。
- 修改 `services/openai_backend_api.py`：新增 Codex 文本 payload 构造和 SSE 请求方法。
- 修改 `services/protocol/openai_v1_models.py`：有合格 Codex 账户时暴露 `gpt-5.5`。
- 修改 `services/protocol/openai_v1_chat_complete.py`：增加 Chat Completions Codex 同步/流式适配。
- 修改 `services/protocol/openai_v1_response.py`：增加 Responses Codex 同步/流式适配。
- 新建 `test/test_codex_text_transport.py`：输入转换、传输 payload、SSE 提取和账户路由测试。
- 新建 `test/test_codex_text_protocols.py`：两个外部协议的同步/流式及错误测试。
- 修改 `test/test_v1_models.py`、`test/test_account_model_allowlist.py`：模型暴露和 source_type 过滤回归。

### Task 1：模型识别、账户过滤与模型列表

**文件：**

- 修改：`utils/helper.py`
- 修改：`services/account_service.py`
- 修改：`services/protocol/openai_v1_models.py`
- 修改：`test/test_account_model_allowlist.py`
- 修改：`test/test_v1_models.py`

**接口：**

- 产出：`CODEX_TEXT_MODEL = "gpt-5.5"`
- 产出：`is_codex_text_model(model: object) -> bool`
- 产出：`AccountService.get_text_access_token(model="auto", excluded_tokens=None, source_type=None) -> str`
- 供后续任务使用：Codex 文本调用通过 `source_type="codex"` 强制选号。

- [ ] **步骤 1：先写模型识别和账户过滤失败测试**

在 `test/test_account_model_allowlist.py` 增加：

```python
def test_text_selection_can_require_codex_source(self) -> None:
    self.service.add_account_items([
        {"access_token": "token-web", "source_type": "web", "allowed_models": ["gpt-5.5"]},
        {"access_token": "token-codex", "source_type": "codex", "allowed_models": ["gpt-5.5"]},
    ])
    self.service.refresh_access_token = lambda token, **_: token

    token = self.service.get_text_access_token("gpt-5.5", source_type="codex")

    self.assertEqual(token, "token-codex")

def test_text_selection_rejects_web_fallback_for_codex_source(self) -> None:
    self.service.add_account_items([
        {"access_token": "token-web", "source_type": "web", "allowed_models": ["gpt-5.5"]},
    ])
    self.service.refresh_access_token = lambda token, **_: token

    with self.assertRaisesRegex(RuntimeError, "no available account supports model gpt-5.5"):
        self.service.get_text_access_token("gpt-5.5", source_type="codex")
```

在 `test/test_v1_models.py` 增加 Codex 账户存在/不存在两个测试，断言 `gpt-5.5` 条件暴露且 `gpt-5-5` 上游模型列表不受影响。

- [ ] **步骤 2：运行测试确认 RED**

运行：

```powershell
python -m unittest test.test_account_model_allowlist test.test_v1_models -v
```

预期：新增测试因 `get_text_access_token()` 不接受 `source_type`、模型列表未追加 `gpt-5.5` 而失败。

- [ ] **步骤 3：实现最小模型与账户路由**

在 `utils/helper.py` 增加：

```python
CODEX_TEXT_MODEL = "gpt-5.5"

def is_codex_text_model(model: object) -> bool:
    return str(model or "").strip().lower() == CODEX_TEXT_MODEL
```

扩展 `get_text_access_token()`：

```python
def get_text_access_token(
        self,
        model: str = "auto",
        excluded_tokens: set[str] | None = None,
        source_type: str | None = None,
) -> str:
    excluded = set(excluded_tokens or set())
    with self._lock:
        candidates = [
            token
            for account in self._accounts.values()
            if account.get("status") not in {"禁用", "异常"}
               and self.account_allows_model(account, model)
               and self._account_matches_source_type(account, source_type)
               and (token := account.get("access_token") or "")
               and token not in excluded
        ]
        if not candidates:
            raise AccountModelUnavailableError(model)
        access_token = candidates[self._index % len(candidates)]
        self._index += 1
    return self.refresh_access_token(access_token, event="get_text_access_token") or access_token
```

在 `openai_v1_models.list_models()` 中，仅当存在状态正常、`source_type=codex` 且 `account_allows_model(account, CODEX_TEXT_MODEL)` 的账户时加入 `gpt-5.5`。

- [ ] **步骤 4：运行任务 1 测试确认 GREEN**

运行：

```powershell
python -m unittest test.test_account_model_allowlist test.test_v1_models test.test_model_account_routing -v
```

预期：全部通过；现有只传 `model` 的调用仍兼容。

### Task 2：Codex 多模态输入、传输与 SSE 文本提取

**文件：**

- 新建：`services/protocol/codex_text.py`
- 修改：`services/openai_backend_api.py`
- 新建：`test/test_codex_text_transport.py`

**接口：**

- 产出：`CodexTextRequest(model, instructions, input_items, reasoning_effort="low", account_email="")`
- 产出：`codex_messages(messages, instructions="") -> tuple[str, list[dict[str, Any]]]`
- 产出：`stream_codex_text_deltas(request: CodexTextRequest) -> Iterator[str]`
- 产出：`collect_codex_text(request: CodexTextRequest) -> str`
- 产出：`OpenAIBackendAPI.iter_codex_text_response_events(...)`

- [ ] **步骤 1：写输入转换与非法图片失败测试**

在 `test/test_codex_text_transport.py` 写测试，覆盖：

```python
instructions, input_items = codex_messages([
    {"role": "system", "content": "system rule"},
    {"role": "developer", "content": "developer rule"},
    {"role": "user", "content": [
        {"type": "text", "text": "template"},
        {"type": "image_url", "image_url": {"url": "https://example.test/a.png"}},
        {"type": "input_image", "image_url": "data:image/png;base64,AAAA"},
    ]},
])
self.assertEqual(instructions, "system rule\n\ndeveloper rule")
self.assertEqual(input_items[0]["role"], "user")
self.assertEqual([part["type"] for part in input_items[0]["content"]], ["input_text", "input_image", "input_image"])
```

分别断言 `file_id`、`ftp://...` 和空图片 URL 抛出 HTTP 400。

- [ ] **步骤 2：运行输入测试确认 RED**

运行：

```powershell
python -m unittest test.test_codex_text_transport.CodexTextInputTests -v
```

预期：因 `services.protocol.codex_text` 尚不存在而失败。

- [ ] **步骤 3：实现输入规范化最小代码**

在 `codex_text.py` 实现：

```python
@dataclass
class CodexTextRequest:
    model: str
    instructions: str
    input_items: list[dict[str, Any]]
    reasoning_effort: str = "low"
    account_email: str = ""

def normalize_codex_image_url(value: object) -> str:
    if isinstance(value, dict):
        if value.get("file_id"):
            raise HTTPException(status_code=400, detail={"error": "file_id is not supported for gpt-5.5"})
        value = value.get("url") or value.get("image_url")
    url = str(value or "").strip()
    if not url.startswith(("http://", "https://", "data:image/")):
        raise HTTPException(status_code=400, detail={"error": "image_url must use http(s) or data:image"})
    return url
```

`codex_messages()` 必须保留 user/assistant 顺序，将 system/developer 文本合并到 instructions，并把 `text/input_text/output_text` 与 `image_url/input_image` 统一转换为 Codex Responses 内容部件。

- [ ] **步骤 4：写 payload 与 SSE 解析失败测试**

用 fake `urllib.request.urlopen` 断言请求：

```python
self.assertEqual(payload["model"], "gpt-5.5")
self.assertEqual(payload["reasoning"], {"effort": "low"})
self.assertEqual(payload["instructions"], "system rule")
self.assertEqual(payload["input"], input_items)
self.assertFalse(payload["store"])
self.assertTrue(payload["stream"])
self.assertNotIn("tools", payload)
```

构造 SSE 事件覆盖 `response.output_text.delta`、`response.output_text.done`、`response.completed`、`response.failed`、只有 completed 嵌套 output、delta+done 去重和无文本终态。

- [ ] **步骤 5：运行传输测试确认 RED**

运行：

```powershell
python -m unittest test.test_codex_text_transport.CodexTextTransportTests -v
```

预期：因 `iter_codex_text_response_events()` 与文本增量执行器尚不存在而失败。

- [ ] **步骤 6：实现独立 Codex 文本传输与执行器**

在 `OpenAIBackendAPI` 增加：

```python
def iter_codex_text_response_events(
        self,
        instructions: str,
        input_items: list[dict[str, Any]],
        model: str = CODEX_TEXT_MODEL,
        reasoning_effort: str = "low",
) -> Iterator[Dict[str, Any]]:
    if not self.access_token:
        raise RuntimeError("access_token is required for codex text endpoints")
    self._ensure_codex_source_account()
    payload = {
        "model": model,
        "reasoning": {"effort": reasoning_effort},
        "instructions": instructions,
        "store": False,
        "input": input_items,
        "stream": True,
    }
    # POST /backend-api/codex/responses；沿用现有 header、事件迭代和 UpstreamHTTPError 转换。
```

在 `codex_text.py` 中实现文本事件提取，跟踪已输出文本，避免 done/completed 重复。`stream_codex_text_deltas()` 使用：

```python
token = account_service.get_text_access_token(
    request.model,
    excluded_tokens=attempted_tokens,
    source_type="codex",
)
```

失败且尚未输出文本时允许换另一个 Codex 账户；已输出后直接抛错。无最终文本必须抛错。

- [ ] **步骤 7：运行任务 2 测试确认 GREEN**

运行：

```powershell
python -m unittest test.test_codex_text_transport test.test_codex_image_output_format -v
```

预期：全部通过，Codex 生图 payload 回归不变。

### Task 3：Chat Completions 同步与流式适配

**文件：**

- 修改：`services/protocol/openai_v1_chat_complete.py`
- 新建：`test/test_codex_text_protocols.py`

**接口：**

- 消费：`is_codex_text_model()`、`codex_messages()`、`CodexTextRequest`、`stream_codex_text_deltas()`。
- 产出：现有 `chat.completion` 与 `chat.completion.chunk` 兼容结构。

- [ ] **步骤 1：写 Chat Completions 失败测试**

覆盖：

```python
body = {
    "model": "gpt-5.5",
    "messages": [{"role": "user", "content": [
        {"type": "text", "text": "build prompt"},
        {"type": "image_url", "image_url": {"url": "https://example.test/product.png"}},
    ]}],
}
```

mock `stream_codex_text_deltas()` 输出 `"new "`、`"prompt"`，断言非流式 `choices[0].message.content == "new prompt"`；流式断言 role chunk、两个内容增量和 stop chunk。另测 `gpt-5-5` 仍调用 `text_backend()`。

对非空 `tools` 或 `tool_choice` 断言 HTTP 400。

- [ ] **步骤 2：运行 Chat 测试确认 RED**

运行：

```powershell
python -m unittest test.test_codex_text_protocols.CodexChatCompletionTests -v
```

预期：`gpt-5.5` 仍进入 Web 文本链路或缺少 Codex 适配函数，测试失败。

- [ ] **步骤 3：实现 Chat 适配最小代码**

在 `handle()` 最前面的流式/非流式分支中，在 Web Search 与普通文本处理前精确识别 `gpt-5.5`：

```python
if is_codex_text_model(body.get("model")):
    return stream_codex_chat_completion(body) if body.get("stream") else codex_chat_completion_response(body)
```

两个函数共用 `codex_messages(chat_messages_from_body(body))` 和 `CodexTextRequest`。同步调用收集增量后使用 `completion_response()`；流式复用 `completion_chunk()`，只输出最终文本增量。内部 `_account_email` 继续供日志层读取。

- [ ] **步骤 4：运行 Chat 测试确认 GREEN**

运行：

```powershell
python -m unittest test.test_codex_text_protocols.CodexChatCompletionTests test.test_model_account_routing test.test_chat_completion_cache -v
```

预期：全部通过。

### Task 4：Responses 同步与流式适配

**文件：**

- 修改：`services/protocol/openai_v1_response.py`
- 修改：`test/test_codex_text_protocols.py`

**接口：**

- 消费：任务 2 的 Codex 文本接口。
- 产出：现有 Responses created/delta/done/completed 兼容事件。

- [ ] **步骤 1：写 Responses 失败测试**

使用：

```python
body = {
    "model": "gpt-5.5",
    "instructions": "generate a new prompt",
    "input": [{"role": "user", "content": [
        {"type": "input_text", "text": "template"},
        {"type": "input_image", "image_url": "data:image/png;base64,AAAA"},
    ]}],
}
```

断言同步响应 `status=completed`、`output[0].content[0].text` 为完整文本；流式事件顺序包含 created、output_item.added、output_text.delta、output_text.done、output_item.done、response.completed。非空 `tools/tool_choice` 返回 400。

- [ ] **步骤 2：运行 Responses 测试确认 RED**

运行：

```powershell
python -m unittest test.test_codex_text_protocols.CodexResponsesTests -v
```

预期：请求仍进入普通 Web `stream_text_response()`，测试失败。

- [ ] **步骤 3：实现 Responses 适配最小代码**

在 `response_events()` 的图片工具判断之前精确识别 `gpt-5.5`：

```python
if is_codex_text_model(body.get("model")):
    yield from stream_codex_text_response(body)
    return
```

`stream_codex_text_response()` 复用现有 `response_created()`、`text_output_item()` 和 `response_completed()`；输入通过 `messages_from_input()` 后进入 `codex_messages()`。同步 `handle()` 继续用现有 `collect_response()` 收集 completed 事件。

- [ ] **步骤 4：运行 Responses 测试确认 GREEN**

运行：

```powershell
python -m unittest test.test_codex_text_protocols.CodexResponsesTests test.test_chat_completion_cache -v
```

预期：全部通过。

### Task 5：全量回归、静态检查和设计验收

**文件：**

- 复核：`docs/superpowers/specs/2026-07-15-codex-gpt-5-5-text-design.md`
- 复核：本计划涉及的全部代码和测试文件。

- [ ] **步骤 1：运行聚焦测试集**

```powershell
python -m unittest test.test_codex_text_transport test.test_codex_text_protocols test.test_account_model_allowlist test.test_model_account_routing test.test_v1_models test.test_codex_image_output_format test.test_chat_completion_cache -v
```

预期：全部通过，无 warning 或 error。

- [ ] **步骤 2：运行完整 unittest 测试发现**

```powershell
python -m unittest discover -s test -p "test_*.py" -v
```

预期：全部通过；如仓库已有依赖真实服务的测试失败，必须单独列出失败用例、失败原因和与本变更的关系，不能笼统声明全量通过。

- [ ] **步骤 3：运行语法和 diff 检查**

```powershell
python -m compileall api services utils test
git diff --check
git status --short
```

预期：`compileall` 与 `git diff --check` 退出码为 0；`git status` 只显示本任务文件和用户原有修改。

- [ ] **步骤 4：逐项核对设计契约**

确认：

- `gpt-5.5` 只走 Codex，`gpt-5-5` 只走 Web。
- 两个入口同步/流式均有测试。
- 文本、多图、HTTP(S)、data URL 均有测试。
- reasoning、原始 SSE 和 base64 不会出现在对外响应或日志。
- 工具、非法图片、无账户、上游失败、无文本终态均有明确错误。
- Codex 生图回归测试通过。

- [ ] **步骤 5：交付工作区变更，不执行 Git 写操作**

报告修改文件、验证命令及精确结果；不执行暂存、提交、推送或 PR。
