# 完整日志返回内容与单滚动条实施计划

> **给 agentic workers：** 必须使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans` 按任务逐步执行本计划。步骤使用 checkbox（`- [ ]`）语法跟踪。

**目标：** 日志详情持久化完整的文本返回内容，并在详情弹窗中仅保留外层滚动条。

**架构：** 后端 `LoggedCall` 不再将普通与流式响应正文截断为 12,000 字符，详情接口继续按既有方式将 `response_text` 与列表响应隔离。前端去除各文本 `<pre>` 的独立高度和滚动，交由日志详情弹窗正文容器统一纵向滚动。

**技术栈：** Python `unittest`、FastAPI 服务层、Next.js/React、Tailwind CSS。

## 全局约束

- 只取消日志管理调用结果正文的 12,000 字符截断；请求正文、列表响应裁剪和图片数据 URL 过滤保持不变。
- 不改变日志详情的复制内容或现有图片预览行为。
- 日志详情弹窗在任一标签页只显示一个纵向滚动条。

---

### Task 1: 完整保存调用与流式调用的返回正文

**文件：**
- 修改：`test/test_log_response_text.py`
- 修改：`services/log_service.py`

**接口：**
- 消费：`LoggedCall.log(..., result=...)` 与 `LoggedCall.stream(items)`。
- 产出：日志详情的 `detail.response_text` 是完整的非图片文本，且不再写入 `response_text_truncated`。

- [ ] **Step 1: 写入失败测试**

```python
def test_log_keeps_response_text_beyond_previous_limit(self) -> None:
    response_text = "x" * 12_001
    call = LoggedCall(IDENTITY, "/v1/chat/completions", "auto", "文本生成")

    call.log("调用完成", {"choices": [{"message": {"content": response_text}}]})

    detail = self._last_detail()
    self.assertEqual(detail.get("response_text"), response_text)
    self.assertNotIn("response_text_truncated", detail)
```

- [ ] **Step 2: 运行测试，确认因截断而失败**

运行：`python -m unittest test.test_log_response_text.LoggedCallResponseTextTests.test_log_keeps_response_text_beyond_previous_limit -v`

预期：失败，`response_text` 只有 12,000 个字符或详情带有截断标识。

- [ ] **Step 3: 用最小改动取消响应正文截断**

```python
# services/log_service.py
# stream(): 每个事件的文本直接追加
if response_text:
    response_parts.append(response_text)

# log(): 直接保存完整文本，不写 response_text_truncated
full_response_text = response_text or _collect_response_text(result)
if full_response_text:
    detail["response_text"] = full_response_text
```

删除仅为截断服务的 `MAX_RESPONSE_TEXT_CHARS`、`response_chars` 与 `_response_excerpt` 调用；保留 `_clean_response_text` 对内嵌图片 data URL 的过滤。

- [ ] **Step 4: 运行目标测试与原有日志正文测试**

运行：`python -m unittest test.test_log_response_text -v`

预期：所有测试通过。

### Task 2: 日志详情文本内容只使用弹窗外层滚动

**文件：**
- 修改：`web/src/app/logs/page.tsx:542-547,570-574,580-584`

**接口：**
- 消费：日志详情外层正文 `<div className="flex-1 overflow-y-auto ...">`。
- 产出：结果内容、请求内容与原始数据标签中的 `<pre>` 不生成内部纵向滚动。

- [ ] **Step 1: 写入失败的静态 UI 断言**

若仓库没有前端测试运行器，在 `web/src/app/logs/page.tsx` 的三个 `<pre>` 区域分别手动验证以下断言：不包含 `max-h-[360px]`、`max-h-[460px]` 或 `overflow-auto`，且外层正文仍保留 `overflow-y-auto`。

- [ ] **Step 2: 移除三个文本 `<pre>` 的内层滚动样式**

```tsx
<pre className="whitespace-pre-wrap break-words rounded-md bg-stone-50 p-4 text-sm leading-6 text-stone-700">
  {responseResult.text}
</pre>
```

对请求文本保持相同的 `text-sm` 类，对原始数据保持 `text-xs leading-5`，仅去除 `max-h-*` 与 `overflow-auto`。

- [ ] **Step 3: 清理失效的截断提示和状态变量**

删除 `responseTextTruncated` 变量与“返回内容已截断，日志仅保留前 12,000 个字符。”提示，因为新的日志不会产生该状态。

- [ ] **Step 4: 构建前端**

运行：`npm run build`

工作目录：`web`

预期：Next.js 构建成功，且 TypeScript 未报告已删除状态变量的引用。

- [ ] **Step 5: 手动验证详情弹窗**

打开 `/logs` 中含长返回内容的日志详情，依次查看“结果内容”“请求内容”“原始数据”。确认窗口右侧只有详情正文的一个滚动条；切换标签和复制结果仍可用。

## 自检

- 覆盖：Task 1 消除持久化截断，Task 2 消除截图所示的双滚动条。
- 占位扫描：无 `TODO`、`TBD` 或未定义的实现步骤。
- 一致性：后端产出字段仍为 `detail.response_text`，前端继续读取该字段；不引入新的接口或数据库迁移。
