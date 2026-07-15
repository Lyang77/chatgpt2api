# Codex GPT-5.6 文本模型接入实施计划

> **给 agentic workers：** 必须使用 superpowers:subagent-driven-development 按任务执行；测试任务严格遵循 superpowers:test-driven-development。步骤使用 checkbox（`- [ ]`）跟踪。

**目标：** 在现有 `gpt-5.5` Codex 文本通道中接入 `gpt-5.6-terra`、`gpt-5.6-luna`、`gpt-5.6-sol`，保留同步、流式和图片理解能力。

**架构：** 将单一 Codex 文本模型常量扩展为显式模型集合，协议层继续通过一个谓词选择现有 Codex adapter，transport 原样传递客户端模型 ID。模型列表逐模型检查本地 Codex 账户状态和 `allowed_models`，不硬编码套餐权限。

**技术栈：** Python、FastAPI、`unittest`、`unittest.mock`、现有 `/backend-api/codex/responses` SSE transport。

## 全局约束

- 只接入 `gpt-5.5`、`gpt-5.6-terra`、`gpt-5.6-luna`、`gpt-5.6-sol` 四个精确 ID。
- 不接受大小写、首尾空格、`gpt-5.6`、`gpt-5.6-sol-pro` 或其他通配变体。
- 四个模型统一走现有 Codex 文本通道，禁止回退到 Web 文本通道。
- 上游 payload 和兼容响应必须保留请求中的精确模型 ID。
- Codex 文本默认使用 `reasoning.effort=low`；合法的请求级 `reasoning_effort`、`thinking_effort` 或 `reasoning.effort` 覆盖默认值。
- 继续支持 Chat Completions、Responses、同步、流式、HTTP(S)/data URL 单图和多图输入。
- 不新增工具调用、Web Search、图片生成、套餐硬编码或新 transport。
- 不使用 worktree，不执行 `git add`、`git commit`、`git push`。

---

### Task 1：模型集合与模型列表 RED 测试

**Files:**
- Modify: `test/test_account_model_allowlist.py`
- Modify: `test/test_v1_models.py`

**Interfaces:**
- Consumes: 现有 `CODEX_TEXT_MODEL`、`is_codex_text_model(model)`、`openai_v1_models.list_models()`。
- Produces: 期望的新常量 `CODEX_TEXT_MODELS: tuple[str, ...]` 及逐模型模型列表行为的失败测试。

- [ ] **Step 1: 写模型集合失败测试**

在 `test/test_account_model_allowlist.py` 导入 `CODEX_TEXT_MODELS`，断言：

```python
self.assertEqual(
    CODEX_TEXT_MODELS,
    ("gpt-5.5", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.6-sol"),
)
for model in CODEX_TEXT_MODELS:
    self.assertTrue(is_codex_text_model(model))
for model in ("gpt-5-5", "gpt-5.6", "gpt-5.6-sol-pro", "GPT-5.6-SOL", " gpt-5.6-sol "):
    self.assertFalse(is_codex_text_model(model))
```

- [ ] **Step 2: 写逐模型暴露失败测试**

在 `test/test_v1_models.py` 构造一个 `source_type=codex`、`allowed_models=["gpt-5.6-terra"]` 的正常账号，断言结果包含 `gpt-5.6-terra`，且不包含 `gpt-5.5`、`gpt-5.6-luna`、`gpt-5.6-sol`。再构造一个未配置 `allowed_models` 的正常 Codex 账号，断言四个模型全部暴露。

- [ ] **Step 3: 运行测试并确认 RED**

Run:

```powershell
python -m unittest test.test_account_model_allowlist test.test_v1_models
```

Expected: FAIL，原因是 `CODEX_TEXT_MODELS` 尚不存在，或 GPT-5.6 模型尚未被识别/暴露；不得是语法错误或测试装配错误。

- [ ] **Step 4: 报告 RED 证据**

记录新增测试名、命令、失败摘要；本任务不得修改生产代码。

---

### Task 2：Chat 与 Responses 路由 RED 测试

**Files:**
- Modify: `test/test_codex_text_protocols.py`

**Interfaces:**
- Consumes: `openai_v1_chat_complete.handle()`、`openai_v1_response.handle()`、`stream_codex_text_deltas()`。
- Produces: 三个 GPT-5.6 模型进入现有 Codex adapter并原样保留模型 ID 的失败测试。

- [ ] **Step 1: 写 Chat Completions 路由失败测试**

对 `gpt-5.6-terra`、`gpt-5.6-luna`、`gpt-5.6-sol` 分别调用非流式 Chat handler；mock `stream_codex_text_deltas()` 返回文本，并断言其收到的 `request.model` 等于当前模型，兼容响应中的 `model` 也等于当前模型。mock Web backend 为抛错，证明请求没有进入 Web 路由。

- [ ] **Step 2: 写 Responses 路由失败测试**

对三个模型分别调用非流式 Responses handler；断言 Codex delta mock 收到精确模型 ID，最终响应 `model` 保持该 ID，并证明 Web backend 未被调用。

- [ ] **Step 3: 写模型化错误文案失败测试**

使用 `gpt-5.6-sol` 携带非空 `tools`，分别断言 Chat 与 Responses 返回 HTTP 400，错误文本包含 `gpt-5.6-sol`，不得硬编码成 `gpt-5.5`。

- [ ] **Step 4: 运行测试并确认 RED**

Run:

```powershell
python -m unittest test.test_codex_text_protocols
```

Expected: FAIL，原因是 GPT-5.6 当前进入 Web 路由或错误文本仍是 `gpt-5.5`；不得是测试代码错误。

- [ ] **Step 5: 报告 RED 证据**

记录新增测试名、命令和失败摘要；本任务不得修改生产代码。

---

### Task 3：最小生产实现与 GREEN 验证

**Files:**
- Modify: `utils/helper.py`
- Modify: `services/protocol/openai_v1_models.py`
- Modify: `services/protocol/openai_v1_chat_complete.py`
- Modify: `services/protocol/openai_v1_response.py`
- Test: `test/test_account_model_allowlist.py`
- Test: `test/test_v1_models.py`
- Test: `test/test_codex_text_protocols.py`
- Test: `test/test_codex_text_transport.py`

**Interfaces:**
- Consumes: Task 1 和 Task 2 的 RED 测试。
- Produces: `CODEX_TEXT_MODELS: tuple[str, ...]`、兼容保留的 `CODEX_TEXT_MODEL = "gpt-5.5"`、集合成员判断及逐模型暴露。

- [ ] **Step 1: 实现显式模型集合**

在 `utils/helper.py` 使用以下稳定顺序，保留旧常量兼容现有默认值：

```python
CODEX_TEXT_MODEL = "gpt-5.5"
CODEX_TEXT_MODELS = (
    CODEX_TEXT_MODEL,
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "gpt-5.6-sol",
)
CODEX_TEXT_MODEL_SET = frozenset(CODEX_TEXT_MODELS)

def is_codex_text_model(model: object) -> bool:
    return str(model or "") in CODEX_TEXT_MODEL_SET
```

- [ ] **Step 2: 实现逐模型列表暴露**

在 `services/protocol/openai_v1_models.py` 导入 `CODEX_TEXT_MODELS`，替换单模型判断：

```python
for model in CODEX_TEXT_MODELS:
    if any(
        account_service._is_locally_available(account)
        and account_service._account_matches_source_type(account, "codex")
        and account_service.account_allows_model(account, model)
        for account in accounts
    ):
        dynamic_models.add(model)
```

- [ ] **Step 3: 错误文本使用实际模型 ID**

Chat 和 Responses 构造请求时先取得 `model = str(body.get("model") or "")`，工具拒绝和空输入错误使用 `f"{model} does not support tools"`、`f"messages are required for {model}"` 或 `f"input is required for {model}"`。不改变 HTTP 状态和错误结构。

- [ ] **Step 4: 运行 RED 测试并确认 GREEN**

Run:

```powershell
python -m unittest test.test_account_model_allowlist test.test_v1_models test.test_codex_text_protocols test.test_codex_text_transport
```

Expected: PASS。

- [ ] **Step 5: 运行现有相关回归**

Run:

```powershell
python -m unittest test.test_model_account_routing test.test_openai_protocols test.test_chat_image_generation test.test_response_image_generation
```

若模块名不存在，先使用 `python -m unittest discover -s test -p "test_*protocol*.py"` 识别实际可运行集合，并在报告中如实记录替代命令与原因。

- [ ] **Step 6: 静态验证**

Run:

```powershell
python -m compileall utils services test
git diff --check
```

Expected: 两个命令均退出 0；允许 Git 的 CRLF 提示，但不允许 whitespace error。

- [ ] **Step 7: 自审并报告**

确认 transport 未新增、模型 ID 未改写、图片转换逻辑未分叉、未知 GPT-5.6 仍走原有非 Codex 路由。不得执行任何 Git 写操作。

---

### Task 4：独立终审

**Files:**
- Review: 本计划涉及的全部 diff。

**Interfaces:**
- Consumes: Task 1-3 的代码、测试和报告。
- Produces: 规格符合性与代码质量两个结论，以及 Critical/Important/Minor 分级问题。

- [ ] **Step 1: 规格复核**

逐项核对四个精确模型、Codex-only 路由、图片理解复用、模型 ID 透传、逐账户白名单暴露和未知变体排除。

- [ ] **Step 2: 代码质量复核**

重点检查常量兼容、重复逻辑、错误文案、测试是否真正证明 Web 路由未被调用，以及是否误改现有 `gpt-5.5`/图片模型行为。

- [ ] **Step 3: 最终验证**

主 agent 根据终审结论修复所有 Critical/Important，再重跑 Task 3 Step 4-6 的命令并报告实际结果。
