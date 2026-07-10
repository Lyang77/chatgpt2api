# 账号模型白名单实施计划

> **给 agentic workers：** 必须使用 `superpowers:executing-plans` 按任务逐步执行本计划。步骤使用 checkbox（`- [ ]`）语法跟踪。

**目标：** 为每个号池账号配置严格模型白名单，并在文本、图片、Responses、Anthropic 和搜索请求的选号阶段强制执行。

**架构：** `AccountService` 规范化和匹配 `allowed_models`，协议层只传递请求模型，前端只编辑展示字段。图片候选同时满足既有套餐、来源和新模型条件。

**技术栈：** Python 3.13+、FastAPI、JSON account storage、React/Next.js、TypeScript、unittest。

## 全局约束

- `allowed_models=[]` 表示不限制，兼容历史账号。
- 非空白名单仅精确匹配小写模型 ID，不隐式匹配别名、套餐前缀或其他模型。
- `auto` 不得选择配置了白名单的账号。
- 无匹配账号报错 `no available account supports model <model>`，不降级或匿名绕过。
- 不覆盖、暂存或提交当前工作区与该功能无关的改动。

## 文件结构

- `services/account_service.py`：字段规范化与文本/图片候选过滤。
- `services/protocol/conversation.py`：文本初选、重试与图片选号的模型透传。
- `services/protocol/openai_v1_chat_complete.py`、`openai_v1_response.py`、`anthropic_v1_messages.py`、`openai_search.py`、`web_search_tool.py`：协议入口传递模型。
- `api/accounts.py`：接收 `allowed_models` 更新。
- `web/src/lib/api.ts`、`web/src/app/accounts/page.tsx`：编辑和展示白名单。
- `test/test_account_model_allowlist.py`、`test/test_model_account_routing.py`：领域和协议回归。

### 任务 1：账号白名单领域逻辑

**文件：**
- 创建：`test/test_account_model_allowlist.py`
- 修改：`services/account_service.py`

**接口：**
- `AccountService._normalize_allowed_models(value: object) -> list[str]`
- `AccountService.account_allows_model(account: dict, model: str) -> bool`
- `AccountService.get_text_access_token(model: str = "auto", excluded_tokens: set[str] | None = None) -> str`
- 图片候选方法增加 `model: str = ""`。

- [x] **步骤 1：写失败测试**

```python
def test_text_selection_uses_exact_account_model_allowlist(self):
    service = AccountService(MemoryStorage())
    service.add_account_items([
        {"access_token": "a", "status": "正常", "allowed_models": ["GPT-5-3", "gpt-5-3"]},
        {"access_token": "b", "status": "正常", "allowed_models": ["gpt-5-5"]},
    ])
    service.refresh_access_token = lambda token, **_: token
    self.assertEqual(service.get_text_access_token("gpt-5-3"), "a")
    self.assertEqual(service.get_text_access_token("gpt-5-5"), "b")
    with self.assertRaisesRegex(RuntimeError, "no available account supports model gpt-5-mini"):
        service.get_text_access_token("gpt-5-mini")
```

- [x] **步骤 2：验证测试失败**

运行：`python -m unittest test.test_account_model_allowlist -v`

预期：当前 `get_text_access_token` 不接收模型参数或未过滤白名单，测试失败。

- [x] **步骤 3：实现最小逻辑**

```python
@staticmethod
def _normalize_allowed_models(value: object) -> list[str]:
    values = value if isinstance(value, list) else []
    return list(dict.fromkeys(str(item or "").strip().lower() for item in values if str(item or "").strip()))

def account_allows_model(self, account: dict, model: str) -> bool:
    allowed = self._normalize_allowed_models(account.get("allowed_models"))
    normalized = str(model or "").strip().lower()
    return not allowed or (normalized != "auto" and normalized in allowed)
```

在 `_normalize_account()` 写入 `allowed_models`；文本候选、图片候选和图片远程刷新后二次校验均调用该匹配函数。

- [x] **步骤 4：补齐边界并验证**

覆盖空列表、`auto`、图片套餐/来源叠加过滤和更新持久化。运行：`python -m unittest test.test_account_model_allowlist test.test_account_image_capabilities -v`。预期全部通过。

### 任务 2：协议入口模型透传

**文件：**
- 创建：`test/test_model_account_routing.py`
- 修改：`services/protocol/conversation.py`
- 修改：`services/protocol/openai_v1_chat_complete.py`
- 修改：`services/protocol/openai_v1_response.py`
- 修改：`services/protocol/anthropic_v1_messages.py`
- 修改：`services/protocol/openai_search.py`
- 修改：`services/protocol/web_search_tool.py`

**接口：**
- `text_backend(model: str) -> OpenAIBackendAPI`
- 重试使用 `get_text_access_token(request.model, attempted_tokens)`。
- 图片使用 `get_available_access_token(..., model=request.model)`。

- [x] **步骤 1：写失败测试**

```python
def test_text_backend_selects_account_for_requested_model(self):
    with mock.patch("services.protocol.conversation.account_service.get_text_access_token", return_value="token-a") as select:
        backend = text_backend("gpt-5-3")
    select.assert_called_once_with("gpt-5-3")
    self.assertEqual(backend.access_token, "token-a")
```

- [x] **步骤 2：验证测试失败**

运行：`python -m unittest test.test_model_account_routing -v`

预期：`text_backend()` 当前无模型参数，测试失败。

- [x] **步骤 3：实现透传**

```python
def text_backend(model: str) -> OpenAIBackendAPI:
    return OpenAIBackendAPI(access_token=account_service.get_text_access_token(model))
```

Chat Completions、Responses、Anthropic 使用其已解析模型；搜索使用固定 `SEARCH_MODEL`；图片流传 `request.model`。

- [x] **步骤 4：验证协议回归**

运行：`python -m unittest test.test_model_account_routing test.test_v1_images_edits_json test.test_codex_image_output_format -v`。预期全部通过。

### 任务 3：账号更新 API 与前端编辑

**文件：**
- 修改：`api/accounts.py`
- 修改：`web/src/lib/api.ts`
- 修改：`web/src/app/accounts/page.tsx`

**接口：**
- `AccountUpdateRequest.allowed_models: list[str] | None = None`
- `updateAccount(accessToken, { allowed_models?: string[] })`
- `Account.allowed_models?: string[]`

- [x] **步骤 1：写失败 API 测试**

```python
def test_account_update_persists_allowed_models(self):
    response = client.post("/api/accounts/update", json={
        "access_token": "token-a",
        "allowed_models": ["GPT-5-3", "gpt-5-3", "gpt-5-5"],
    }, headers=AUTH_HEADERS)
    self.assertEqual(response.status_code, 200)
    self.assertEqual(response.json()["item"]["allowed_models"], ["gpt-5-3", "gpt-5-5"])
```

- [x] **步骤 2：验证测试失败**

运行：`python -m unittest test.test_account_model_allowlist.AccountModelAllowlistApiTests -v`

预期：当前 API 未接收或未保存 `allowed_models`。

- [x] **步骤 3：实现 API 与编辑页**

后端仅将非 `None` 的 `allowed_models` 写入账号。前端编辑弹窗使用 `Textarea`，每行或逗号一个模型；提交时切分、trim、去空值。账号列表展示 `不限` 或模型标签，并使用更新 API 返回的账号列表刷新。

- [x] **步骤 4：验证前后端契约**

运行：`python -m unittest test.test_account_model_allowlist -v`。

运行：`npm run build`（目录：`web`）。

预期：Python 测试与 Next.js 生产构建通过。

### 任务 4：回归与差异审查

- [x] **步骤 1：运行相关回归**

运行：`python -m unittest test.test_account_model_allowlist test.test_model_account_routing test.test_account_image_capabilities test.test_account_export test.test_v1_images_edits_json test.test_codex_image_output_format test.test_log_response_text -v`

预期：全部通过。

- [x] **步骤 2：静态检查**

运行：`python -m compileall -q api services test`，随后运行 `git diff --check`。

预期：无语法或空白错误，且差异仅覆盖账号模型白名单边界。
