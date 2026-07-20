# 图片任务排队状态实施计划

> **给 agentic workers：** 必须使用 superpowers:subagent-driven-development 或 superpowers:executing-plans 按任务逐步执行本计划。步骤使用 checkbox 跟踪。

**目标：** 将等待账号槽位的图片任务从 `running` 拆分为 `queued`，使页面状态、账号并发统计和停止交互与真实运行阶段一致。

**架构：** 图片子任务创建时持久化为 `queued/getting_account`；账号选择成功后原记录原地转换为 `running/generating`。停止和启动恢复覆盖两种未完成状态，账号在途查询仍只读取 `running`。

**技术栈：** Python 3.13、FastAPI、SQLite、Next.js 16、React 19、Node test runner。

## 全局约束

- 不新增日志类型，继续使用 `type=call`。
- 只有占用账号槽位的任务使用 `status=running`。
- `queued` 与 `running` 均允许管理员停止，终态不可被迟到结果覆盖。
- 不改变每账号 `image_max_inflight` 的并发实现。

---

### Task 1: 后端状态流转与恢复

**Files:**
- Modify: `services/log_service.py`
- Modify: `services/log_store.py`
- Modify: `services/protocol/conversation.py`
- Test: `test/test_image_subtask_logs.py`
- Test: `test/test_log_store.py`

**Interfaces:**
- Consumes: `ImageTaskLogContext.cancel_event`、`LogService.update_call()`。
- Produces: `queued -> running -> terminal` 状态流、`list_unfinished_image_subtasks()`。

- [x] 先写失败测试：新建图片任务为 `queued`，获取账号后写 `running`，停止接受 `queued/running`，启动恢复同时收口二者。
- [x] 运行 `python -m unittest test.test_image_subtask_logs test.test_log_store`，确认断言因现有 `running` 行为失败。
- [x] 将 `create_image_task_log_context()` 初始状态改为 `queued`；账号获取成功后写入 `status="running"`、`stage="generating"` 和 `account_email`。
- [x] 新增未完成图片任务查询：SQLite 条件使用 `status IN ('queued', 'running')`；账号在途查询保留仅 `running`。
- [x] 让 `request_stop()` 接受 `queued` 与 `running`，启动恢复调用未完成任务查询。
- [x] 重跑测试并确认通过。

### Task 2: 前端友好状态交互

**Files:**
- Modify: `web/src/app/logs/page.tsx`
- Modify: `web/src/lib/log-duration.ts`
- Test: `web/test/log-duration.test.ts`

**Interfaces:**
- Consumes: `SystemLog.detail.status`、`stage`、`endpoint`。
- Produces: “排队中”标签/筛选、排队任务停止文案、排队实时耗时。

- [x] 先写失败测试：`queued` 日志应像 `running` 一样按 `started_at` 实时计算等待时长。
- [x] 运行 `node --experimental-strip-types web/test/log-duration.test.ts`，确认失败。
- [x] 页面增加 `queued -> 排队中` 映射和筛选项，使用中性徽标区分执行中的警示色。
- [x] `queued/running` 图片任务都显示停止按钮；确认框和成功提示分别使用“取消排队任务”和“停止执行任务”。
- [x] 页面存在 `queued` 或 `running` 时继续本地刷新耗时，不增加服务器轮询。
- [x] 重跑前端测试并确认通过。

### Task 3: 文档与完整验证

**Files:**
- Modify: `docs/superpowers/specs/2026-07-10-image-subtask-logs-design.md`

- [x] 更新状态模型、生命周期、停止和启动恢复说明，明确 `queued` 不计入账号在途。
- [x] 运行相关后端测试、`python -m compileall -q api services test`、前端定向 Node 测试和 `npm run build`。
- [x] 运行 `git diff --check`，确认没有空白错误或非目标文件变更。
