# Codex 图片生成质量配置实施计划

> **给 agentic workers：** 必须使用 `superpowers:executing-plans` 在当前会话逐步执行本计划。步骤使用 checkbox（`- [ ]`）跟踪。当前任务不授权 Git 提交。

**目标：** 在配置页面维护 `codex_image_quality`，并按 `auto` 遵循请求、固定等级强制覆盖的规则应用到 `codex-gpt-image-2` 上游载荷。

**架构：** 后端 `ConfigStore` 负责规范化和持久化；Codex 图片上游载荷在唯一组装点计算实际质量。前端新增独立的质量枚举/规范化模块，通过现有 Zustand 设置 Store 和 `/api/settings` 保存。

**技术栈：** Python 3 `unittest`、FastAPI 配置接口、TypeScript、React、Zustand、Next.js、Node.js test runner。

## 全局约束

- 配置键固定为 `codex_image_quality`。
- 允许值固定为 `auto`、`low`、`medium`、`high`，默认和非法回退均为 `auto`。
- `auto` 保留请求中的 `quality`；其他值强制覆盖。
- 只修改 Codex 图片链路，不修改普通 `gpt-image-2` Web 链路。
- 不执行 `git add`、`git commit`、`git push`。

---

### Task 1：后端配置存储

**Files:**
- Modify: `test/test_config.py`
- Modify: `services/config.py`
- Modify: `config.json`

**Interfaces:**
- Produces: `ConfigStore.codex_image_quality -> str`
- Produces: `ConfigStore.get()["codex_image_quality"]`

- [ ] **Step 1：编写失败测试**

在 `test/test_config.py` 增加缺省、合法值、大小写、非法值和持久化测试：

```python
def test_codex_image_quality_is_normalized_and_exposed(self) -> None:
    cases = ((missing, "auto"), (" HIGH ", "high"), ("unexpected", "auto"))

def test_codex_image_quality_update_is_normalized_and_persisted(self) -> None:
    updated = store.update({"codex_image_quality": " HIGH "})
    self.assertEqual(updated["codex_image_quality"], "high")
    self.assertEqual(persisted["codex_image_quality"], "high")
```

- [ ] **Step 2：确认测试因功能缺失而失败**

Run: `python -m unittest test.test_config`

Expected: FAIL，提示 `ConfigStore` 没有 `codex_image_quality` 或返回结果缺少该键。

- [ ] **Step 3：实现最小配置逻辑**

在 `services/config.py` 增加：

```python
def _normalize_codex_image_quality(value: object) -> str:
    normalized = str(value or "auto").strip().lower()
    return normalized if normalized in {"auto", "low", "medium", "high"} else "auto"

@property
def codex_image_quality(self) -> str:
    return _normalize_codex_image_quality(self.data.get("codex_image_quality"))
```

`get()` 暴露规范值，`update()` 在保存前规范化。`config.json` 增加默认配置：

```json
"codex_image_quality": "auto"
```

- [ ] **Step 4：确认后端配置测试通过**

Run: `python -m unittest test.test_config`

Expected: PASS。

### Task 2：Codex 上游质量覆盖

**Files:**
- Modify: `test/test_codex_image_output_format.py`
- Modify: `services/openai_backend_api.py`

**Interfaces:**
- Consumes: `config.codex_image_quality`
- Produces: `/backend-api/codex/responses` 中 `tools[0].quality`

- [ ] **Step 1：编写失败测试**

新增两个载荷断言：

```python
with mock.patch.object(config, "data", {"codex_image_quality": "auto"}):
    # 请求 high，断言 tools[0].quality == "high"

with mock.patch.object(config, "data", {"codex_image_quality": "medium"}):
    # 请求 low，断言 tools[0].quality == "medium"
```

- [ ] **Step 2：确认固定配置覆盖测试失败**

Run: `python -m unittest test.test_codex_image_output_format`

Expected: FAIL，固定 `medium` 时当前载荷仍为请求的 `low`。

- [ ] **Step 3：实现唯一覆盖点**

在 `iter_codex_image_response_events()` 组装载荷前计算：

```python
configured_quality = config.codex_image_quality
effective_quality = str(quality or "auto") if configured_quality == "auto" else configured_quality
```

将 `tools[0].quality` 改为 `effective_quality`。

- [ ] **Step 4：确认 Codex 定向测试通过**

Run: `python -m unittest test.test_codex_image_output_format`

Expected: PASS。

### Task 3：前端配置状态与选项

**Files:**
- Create: `web/src/lib/codex-image-quality.ts`
- Create: `web/src/lib/codex-image-quality.test.ts`
- Modify: `web/src/lib/api.ts`
- Modify: `web/src/app/settings/store.ts`

**Interfaces:**
- Produces: `CodexImageQuality`
- Produces: `CODEX_IMAGE_QUALITY_OPTIONS`
- Produces: `normalizeCodexImageQuality(value)`
- Produces: `SettingsStore.setCodexImageQuality(value)`

- [ ] **Step 1：编写失败测试**

```typescript
import test from "node:test";
import assert from "node:assert/strict";
import { normalizeCodexImageQuality } from "./codex-image-quality.ts";

test("normalizes Codex image quality", () => {
  assert.equal(normalizeCodexImageQuality(undefined), "auto");
  assert.equal(normalizeCodexImageQuality(" HIGH "), "high");
  assert.equal(normalizeCodexImageQuality("unexpected"), "auto");
});
```

- [ ] **Step 2：确认测试因模块缺失而失败**

Run: `node --experimental-strip-types --test web/src/lib/codex-image-quality.test.ts`

Expected: FAIL，提示找不到 `codex-image-quality.ts`。

- [ ] **Step 3：实现枚举、类型和 Store 接线**

```typescript
export type CodexImageQuality = "auto" | "low" | "medium" | "high";
export const CODEX_IMAGE_QUALITY_OPTIONS = [
  { value: "auto", label: "自动" },
  { value: "low", label: "低" },
  { value: "medium", label: "中" },
  { value: "high", label: "高" },
] as const;
```

在 `SettingsConfig`、`normalizeConfig()`、`saveConfig()` 和 Store setter 中接入 `codex_image_quality`。

- [ ] **Step 4：确认前端规范化测试通过**

Run: `node --experimental-strip-types --test web/src/lib/codex-image-quality.test.ts`

Expected: PASS。

### Task 4：配置页面下拉框

**Files:**
- Modify: `web/src/app/settings/components/config-card.tsx`

**Interfaces:**
- Consumes: `CODEX_IMAGE_QUALITY_OPTIONS`
- Consumes: `setCodexImageQuality(value)`

- [ ] **Step 1：在现有配置卡片增加下拉框**

将其放在 `Web 生图思考等级` 后：

```tsx
<label className="text-sm text-stone-700">Codex 生图质量</label>
<Select
  value={String(config?.codex_image_quality || "auto")}
  onValueChange={setCodexImageQuality}
>
  {CODEX_IMAGE_QUALITY_OPTIONS.map((option) => (
    <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>
  ))}
</Select>
```

说明文字为：`仅影响 codex-gpt-image-2；选择低、中或高时会覆盖 API 请求中的 quality。`

- [ ] **Step 2：运行 TypeScript/Next 构建验证**

Run: `npm run build`

Workdir: `web`

Expected: PASS，配置页类型检查和生产构建成功。

### Task 5：回归验证

**Files:**
- Verify all modified files

- [ ] **Step 1：运行后端相关测试**

Run: `python -m unittest test.test_config test.test_codex_image_output_format test.test_image_thinking_effort`

Expected: PASS，Codex 配置生效且 Web 思考等级不受影响。

- [ ] **Step 2：运行前端测试和构建**

Run: `node --experimental-strip-types --test src/lib/codex-image-quality.test.ts`

Run: `npm run build`

Workdir: `web`

Expected: 两条命令均 PASS。

- [ ] **Step 3：检查差异和工作树**

Run: `git diff --check`

Run: `git status --short`

Expected: 无空白错误，只包含本功能的设计、计划、测试和实现文件。
