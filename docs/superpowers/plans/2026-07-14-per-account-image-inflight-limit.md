# 账号级生图在途上限实施计划

> **给 agentic workers：** 使用 `superpowers:executing-plans` 在当前会话逐步执行。步骤使用 checkbox（`- [ ]`）跟踪。

**目标：** 在号池管理中为每个账号配置独立的生图最高在途数量，并让调度器以账号字段为唯一依据。

**架构：** `AccountService` 负责默认值规范化和逐账号容量判断；账号更新 API 暴露字段；号池页面负责展示和编辑。现有条件变量继续承担满载等待和释放唤醒。

**技术栈：** Python、FastAPI、JSON account storage、React、Next.js、TypeScript、pytest。

## 全局约束

- 字段名统一为 `image_max_inflight`。
- 已有账号和新导入账号默认值均为 `3`。
- 值必须是大于等于 `1` 的整数。
- 调度不再读取全局 `image_account_concurrency`。
- 所有账号满载时等待，不降级模型、不降低 `n`、不额外发送上游请求。
- 不提交 Git，不覆盖本任务之前的工作区改动。

## 文件结构

- `services/account_service.py`：字段规范化、逐账号容量过滤和在途展示。
- `api/accounts.py`：账号更新字段与输入校验。
- `web/src/lib/api.ts`：账号类型和更新契约。
- `web/src/app/accounts/page.tsx`：表格显示和编辑输入。
- `web/src/app/settings/components/config-card.tsx`：移除旧全局入口。
- `test/test_account_image_capabilities.py`：默认值与调度容量测试。
- `test/test_account_model_allowlist.py`：账号更新 API 回归。

### 任务 1：账号字段和逐账号调度

- [x] 在 `test/test_account_image_capabilities.py` 添加默认 `3`、自定义容量和满载等待测试。
- [x] 运行定向测试，确认旧代码因缺少 `image_max_inflight` 或仍读取全局值而失败。
- [x] 在 `_normalize_account()` 写入规范化字段，在 `_list_available_candidate_tokens()` 按每条账号自己的上限过滤。
- [x] 运行定向测试，确认等待任务只在槽位释放后获得账号。

### 任务 2：账号更新 API

- [x] 在账号 API 测试中添加更新 `image_max_inflight` 成功和小于 `1` 返回 `422` 的用例。
- [x] 运行测试确认失败。
- [x] 给 `AccountUpdateRequest` 增加 `image_max_inflight: int | None = Field(default=None, ge=1)`，并透传到 `AccountService.update_account()`。
- [x] 运行账号 API 回归。

### 任务 3：号池管理界面

- [x] 在 `Account` 类型和 `updateAccount()` 参数中加入 `image_max_inflight`。
- [x] 在账号表格把在途显示改为 `image_inflight / image_max_inflight`。
- [x] 在编辑弹窗增加“最高在途数量”数值输入，打开时初始化，保存前校验至少为 `1`。
- [x] 从通用配置卡移除“单账号图片并发”输入，保留底层旧配置兼容。
- [x] 运行 `npm run build` 验证 TypeScript 和生产构建。

### 任务 4：回归验证

- [x] 运行账号、图片调度、Codex 和编辑接口相关 pytest。
- [x] 运行 `git diff --check` 并检查差异只覆盖本功能及此前已存在的改动。
