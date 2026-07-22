# 日志列表模型列与筛选条件持久化实施计划

> **给 agentic workers：** 必须使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 按任务逐步执行本计划。步骤使用 checkbox（`- [ ]`）语法跟踪。

**目标：** 在调用日志列表展示模型、隐藏类型列，让已应用的日志筛选条件在 F5 后通过 URL 恢复，并消除日志详情中的双纵向滚动条。

**架构：** 新建纯函数模块负责日志筛选状态与 `URLSearchParams` 的双向转换，页面只负责读取、应用和同步状态。表格沿用现有 `detail` 数据展示模型，不修改后端接口。

**技术栈：** Next.js 16、React 19、TypeScript、Node.js `assert`、Next Router。

## 全局约束

- 只持久化 `type`、`start_date`、`end_date`、`key_name`、`account_email`、`status`、`model`、`endpoint`、`batch_id`、`summary`。
- 页码、每页条数、选择项和弹窗状态不持久化；F5 后回到第 1 页、每页 20 条。
- URL 只保存已经应用的查询条件；文本输入变更后需点击“查询”才同步。
- 日志类型和日期维持现有的即时查询行为，并在变化时同步 URL。
- 不修改日志 API 或存储结构。
- 日志详情保留正文外层滚动，结果、请求和原始数据文本不创建内部纵向滚动区。

---

### 任务 1：日志筛选 URL 纯函数

**文件：**
- 新建：`web/src/lib/log-filters.ts`
- 新建：`web/test/log-filters.test.ts`

**接口：**
- 产出：`LogFilters` 类型。
- 产出：`readLogFilters(searchParams: URLSearchParams): LogFilters`。
- 产出：`writeLogFilters(filters: LogFilters): string`。

- [x] **步骤 1：先写失败测试**

覆盖默认值、完整参数恢复、无效 `type` 回退、空值不写入 URL：

```ts
import { deepEqual, equal } from "node:assert/strict";
import { readLogFilters, writeLogFilters } from "../src/lib/log-filters.ts";

deepEqual(readLogFilters(new URLSearchParams()), {
  type: "call", startDate: "", endDate: "", keyName: "", accountEmail: "",
  status: "", summary: "", model: "", endpoint: "", batchId: "",
});

const restored = readLogFilters(new URLSearchParams(
  "type=account&start_date=2026-07-01&end_date=2026-07-22&key_name=k&account_email=a%40b.com&status=failed&summary=%E6%96%87%E7%94%9F%E5%9B%BE&model=gpt-5&endpoint=%2Fv1%2Fresponses&batch_id=batch-1",
));
equal(restored.type, "account");
equal(restored.model, "gpt-5");
equal(restored.batchId, "batch-1");
equal(readLogFilters(new URLSearchParams("type=unknown")).type, "call");

const written = new URLSearchParams(writeLogFilters({ ...restored, type: "call", model: "", batchId: "" }));
equal(written.get("type"), null);
equal(written.get("model"), null);
equal(written.get("batch_id"), null);
equal(written.get("status"), "failed");
```

- [x] **步骤 2：运行测试并确认按预期失败**

运行：`node --experimental-strip-types web/test/log-filters.test.ts`

预期：FAIL，提示找不到 `web/src/lib/log-filters.ts`。

- [x] **步骤 3：实现最小纯函数**

`readLogFilters` 将 snake_case URL 参数映射为页面 camelCase 状态；`type` 只接受 `call` 或 `account`。`writeLogFilters` 省略空值和默认 `type=call`，其余字段写回约定参数名。

- [x] **步骤 4：运行测试并确认通过**

运行：`node --experimental-strip-types web/test/log-filters.test.ts`

预期：输出 `log filter tests passed`，退出码为 0。

- [x] **步骤 5：提交任务 1**

```powershell
git add -- web/src/lib/log-filters.ts web/test/log-filters.test.ts
git commit -m "test: define persistent log filter contract"
```

### 任务 2：日志页接入 URL 状态并调整列表列

**文件：**
- 修改：`web/src/app/logs/page.tsx`
- 新建：`web/test/log-page-contract.test.ts`

**接口：**
- 消费：`LogFilters`、`readLogFilters`、`writeLogFilters`。
- 页面通过 `useSearchParams()` 读取首次条件，通过 `useRouter().replace()` 更新当前 URL。

- [x] **步骤 1：先写失败的页面契约测试**

测试读取页面源码并断言：导入 URL 筛选 helper；存在“模型”表头及 `getDetailText(item, "model")` 单元格；表格不再包含 `<TableHead>类型</TableHead>` 和对应类型 Badge；详情正文仍有 `overflow-y-auto`，三个文本 `pre` 不再含 `max-h-[360px] overflow-auto` 或 `max-h-[460px] overflow-auto`。

- [x] **步骤 2：运行测试并确认按预期失败**

运行：`node --experimental-strip-types web/test/log-page-contract.test.ts`

预期：FAIL，至少提示缺少模型列、仍存在类型列或文本区仍创建内部滚动。

- [x] **步骤 3：接入初始筛选状态**

使用 `useSearchParams()` 得到初始参数，通过 `useMemo(() => readLogFilters(new URLSearchParams(searchParams.toString())), [searchParams])` 建立初始值，并将十个筛选 `useState` 的初值替换为对应字段。

- [x] **步骤 4：同步已应用条件到 URL**

增加 `syncFiltersToUrl(overrides?: Partial<LogFilters>)`，组合当前筛选状态和覆盖值，调用 `router.replace(query ? "/logs?${query}" : "/logs", { scroll: false })`。查询按钮在请求前同步当前状态；类型和日期 effect 使用本次新值同步；列表“刷新”继续只调用 `loadLogs()`。

- [x] **步骤 5：调整表格列**

删除“类型”表头及行内类型 Badge。在调用日志的“执行账号”后增加 `<TableHead>模型</TableHead>`，行内增加带截断和 `title` 的模型单元格，值取 `getDetailText(item, "model")`。

- [x] **步骤 6：消除日志详情双滚动区**

保留弹窗正文容器的 `flex-1 overflow-y-auto`，从结果内容、请求内容和原始数据的三个 `pre` 中删除 `max-h-[360px] overflow-auto` / `max-h-[460px] overflow-auto`，保留换行、断词、背景和排版样式。

- [x] **步骤 7：运行页面契约测试与筛选测试**

```powershell
node --experimental-strip-types web/test/log-page-contract.test.ts
node --experimental-strip-types web/test/log-filters.test.ts
```

预期：两个脚本均退出码为 0。

- [x] **步骤 8：运行 Web 生产构建**

运行：`npm run build`（工作目录：`web`）。

预期：Next.js 构建成功，无 TypeScript 错误。

- [x] **步骤 9：提交任务 2**

```powershell
git add -- web/src/app/logs/page.tsx web/test/log-page-contract.test.ts docs/superpowers/plans/2026-07-22-log-list-model-and-filter-persistence.md
git commit -m "feat: persist log filters across refresh"
```

### 任务 3：最终回归验证

**文件：**
- 验证：`web/test/*.test.ts`
- 验证：`web/src/app/logs/page.tsx`

- [ ] **步骤 1：运行全部 Web Node 测试**

```powershell
Get-ChildItem -LiteralPath web/test -Filter '*.test.ts' | ForEach-Object { node --experimental-strip-types $_.FullName; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE } }
```

预期：全部测试退出码为 0。

- [ ] **步骤 2：再次运行生产构建**

运行：`npm run build`（工作目录：`web`）。

预期：构建成功。

- [ ] **步骤 3：检查改动范围**

运行：`git status --short`、`git diff --check HEAD~2..HEAD`。

预期：无未提交文件，无空白错误；改动仅涉及计划、日志筛选 helper/测试和日志页。
