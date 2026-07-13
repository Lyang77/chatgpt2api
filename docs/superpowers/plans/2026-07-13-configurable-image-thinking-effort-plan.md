# 可配置的 Web 生图思考等级实施计划

> **给 agentic workers：** 按任务逐步执行本计划并使用 TDD。步骤使用 checkbox（`- [ ]`）跟踪。本次在当前会话内执行，不派生子代理。

**目标：** 在系统配置页维护 `image_thinking_effort`，并将其应用到普通 `gpt-image-2` 的 Web 正式生图请求。

**架构：** 后端 `ConfigStore` 负责唯一的值归一化，图片 Web 请求构造器只消费归一化结果。前端用一个可独立测试的枚举/归一化模块共享下拉选项和保存值，现有 settings store 与配置卡片负责读写和展示。

**技术栈：** Python、unittest/pytest、Next.js 16、React 19、TypeScript、Zustand、Radix Select。

## 全局约束

- 默认值为 `high`。
- 有效值为空字符串、`low`、`medium`、`high`、`extended`。
- 非法值回退为 `high`；空字符串表示关闭且上游载荷省略字段。
- 仅影响 `gpt-image-2 -> gpt-5-3 + picture_v2`，不影响文本和 Codex 图片链路。
- 不增加请求级入参，不修改尺寸、质量、格式或轮询策略。
- 保留工作区现有未提交修改；不执行 `git add`、`commit`、`push`。

---

### Task 1：后端配置契约

**Files:**
- Modify: `services/config.py`
- Test: `test/test_config.py`

**Interfaces:**
- Produces: `ConfigStore.image_thinking_effort -> str`
- Produces: `ConfigStore.get()["image_thinking_effort"]`

- [ ] **Step 1：先写失败测试**

在 `test/test_config.py` 使用临时 `config.json` 构造 `ConfigStore`，分别断言缺失值默认 `high`、空字符串保留关闭、四个合法值原样返回、非法值回退 `high`，并断言 `get()` 暴露归一化值。

- [ ] **Step 2：确认测试因缺少属性而失败**

运行：

```powershell
python -m pytest -q test/test_config.py
```

预期：新增断言以 `AttributeError` 或缺少 `image_thinking_effort` 字段失败。

- [ ] **Step 3：实现最小配置属性**

在 `ConfigStore` 增加：

```python
@property
def image_thinking_effort(self) -> str:
    raw = self.data.get("image_thinking_effort")
    if raw is None:
        return "high"
    normalized = str(raw).strip().lower()
    return normalized if normalized in {"", "low", "medium", "high", "extended"} else "high"
```

并在 `get()` 中写入归一化值。

- [ ] **Step 4：确认配置测试通过**

运行同一 pytest 命令，预期全部通过。

### Task 2：Web 生图 Payload

**Files:**
- Modify: `services/openai_backend_api.py`
- Create: `test/test_image_thinking_effort.py`

**Interfaces:**
- Consumes: `config.image_thinking_effort`
- Produces: 正式 `/backend-api/f/conversation` 请求可选的顶层 `thinking_effort`；prepare 请求保持不变

- [ ] **Step 1：先写失败测试**

使用 mock session 调用 `_prepare_image_conversation()` 和 `_start_image_generation()`，断言配置为 `high` 时 prepare 请求不含该字段，正式请求的 `json` 包含：

```python
{"thinking_effort": "high"}
```

再断言配置为空字符串时两个载荷都不包含该字段。

- [ ] **Step 2：确认 Payload 测试失败**

运行：

```powershell
python -m pytest -q test/test_image_thinking_effort.py
```

预期：当前 payload 不包含 `thinking_effort`，断言失败。

- [ ] **Step 3：实现最小 Payload 注入**

仅在正式请求字典构造完成后执行：

```python
thinking_effort = config.image_thinking_effort
if thinking_effort:
    payload["thinking_effort"] = thinking_effort
```

不改 Codex Responses payload。

- [ ] **Step 4：确认 Payload 测试通过**

运行 Task 1 和 Task 2 的测试，预期全部通过。

### Task 3：前端配置值与状态管理

**Files:**
- Create: `web/src/lib/image-thinking-effort.ts`
- Create: `web/test/image-thinking-effort.test.ts`
- Modify: `web/src/lib/api.ts`
- Modify: `web/src/app/settings/store.ts`

**Interfaces:**
- Produces: `ImageThinkingEffort`、`IMAGE_THINKING_EFFORT_OPTIONS`、`normalizeImageThinkingEffort()`
- Extends: `SettingsConfig.image_thinking_effort?: string`
- Produces: `setImageThinkingEffort(value: string): void`

- [ ] **Step 1：先写失败测试**

测试缺失值默认 `high`、空字符串保持关闭、合法值保留、非法值回退 `high`，并断言下拉值依次为 `""`、`low`、`medium`、`high`、`extended`。

- [ ] **Step 2：确认前端测试因模块不存在而失败**

运行：

```powershell
node --experimental-strip-types web/test/image-thinking-effort.test.ts
```

预期：找不到 `web/src/lib/image-thinking-effort.ts`。

- [ ] **Step 3：实现共享配置模块并接入 store**

共享模块使用：

```ts
export type ImageThinkingEffort = "" | "low" | "medium" | "high" | "extended";

export const IMAGE_THINKING_EFFORT_OPTIONS = [
  { value: "", label: "关闭" },
  { value: "low", label: "低" },
  { value: "medium", label: "中" },
  { value: "high", label: "高" },
  { value: "extended", label: "扩展" },
] as const;
```

`normalizeConfig()` 和 `saveConfig()` 使用同一归一化函数，store 增加 setter。

- [ ] **Step 4：确认共享模块测试通过**

重新运行 Node 测试，预期输出通过提示。

### Task 4：配置页下拉框

**Files:**
- Modify: `web/src/app/settings/components/config-card.tsx`

**Interfaces:**
- Consumes: `IMAGE_THINKING_EFFORT_OPTIONS`
- Consumes: `config.image_thinking_effort` 和 `setImageThinkingEffort()`

- [ ] **Step 1：接入现有 Radix Select**

在图片轮询超时附近增加“Web 生图思考等级”，保持现有两列网格、石色边框和圆角。Radix Select 不能直接使用空字符串 item，因此 UI 使用哨兵值 `off`，选择时映射回空字符串：

```tsx
<Select
  value={config?.image_thinking_effort || "off"}
  onValueChange={(value) => setImageThinkingEffort(value === "off" ? "" : value)}
>
```

说明文案：仅影响 `gpt-image-2` 的 `gpt-5-3 + picture_v2` Web 链路，关闭时不发送思考参数。

- [ ] **Step 2：运行 TypeScript/Next 构建验证**

运行：

```powershell
npm run build
```

工作目录：`web`。预期构建成功。

### Task 5：综合回归验证

**Files:**
- Verify only

- [ ] **Step 1：运行后端定向测试**

```powershell
python -m pytest -q test/test_config.py test/test_image_thinking_effort.py test/test_codex_image_output_format.py
```

预期：全部通过，证明配置契约、Web payload 和 Codex 隔离未回归。

- [ ] **Step 2：运行前端定向测试**

```powershell
node --experimental-strip-types web/test/image-thinking-effort.test.ts
```

预期：输出 `image thinking effort tests passed`。

- [ ] **Step 3：检查差异边界**

运行 `git diff --check` 和目标文件 diff，确认无空白错误且没有覆盖其他未提交修改。

- [ ] **Step 4：记录真实调用边界**

若本地服务和可用 Web 图片账号已就绪，再用 `quality=auto`、默认 `image_thinking_effort=high` 发起一次 `gpt-image-2` 请求；若环境未就绪，明确报告只完成静态载荷验证，不把它描述成真实上游验证。
