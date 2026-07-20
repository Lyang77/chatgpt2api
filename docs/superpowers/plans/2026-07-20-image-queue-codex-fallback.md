# 图片队列 Codex 回退实施计划

> **给 agentic workers：** 必须使用 superpowers:subagent-driven-development 或 superpowers:executing-plans 按任务逐步执行本计划。步骤使用 checkbox 跟踪。

**目标：** `gpt-image-2` 图片子任务等待账号槽位超过 10 秒后，同时接受普通与 Codex 图片账号，使用先获得的单一链路继续执行。

**架构：** 账号服务在同一个条件变量下检查主路由和延迟启用的回退路由，返回 token、实际模型和等待耗时。图片子任务使用独立 `effective_model` 副本选择上游协议，不修改多图任务共享的原始请求。

**技术栈：** Python 3.13、threading.Condition、FastAPI、SQLite。

## 全局约束

- 仅 `requested_model=gpt-image-2` 自动回退到 `codex-gpt-image-2`。
- 10 秒前只选普通链路；10 秒后普通链路优先，同一检查周期普通不可用时选择 Codex。
- 每个子任务只占用一个账号槽位、只发起一次当前尝试的上游调用。
- Codex 不可用时继续等待普通链路，不因回退缺失而失败。
- 停止信号在等待任一路由期间继续生效。

---

### Task 1: 双路由账号选择

**Files:**
- Modify: `services/account_service.py`
- Test: `test/test_account_image_capabilities.py`

- [x] 写失败测试：阈值前普通槽位释放时仍选普通模型；阈值后普通满载时选择空闲 Codex 模型。
- [x] 运行定向测试并确认新 API 尚不存在而失败。
- [x] 新增 `ImageAccountSelection` 和 `get_available_access_token_with_fallback()`，在同一 `Condition` 中等待并保留取消检查。
- [x] 验证选择结果只增加一个账号的 `_image_inflight`。

### Task 2: 子任务模型与日志

**Files:**
- Modify: `services/config.py`
- Modify: `config.json`
- Modify: `services/protocol/conversation.py`
- Test: `test/test_image_subtask_logs.py`

- [x] 写失败测试：回退选择后使用 `stream_codex_image_outputs`，原始 `ConversationRequest.model` 保持不变。
- [x] 增加 `image_codex_fallback_wait_secs`，默认 10 秒。
- [x] 为每个子任务维护 `requested_model` 和 `effective_model`，用 `dataclasses.replace()` 创建尝试请求。
- [x] 日志写入 `model`、`requested_model`、`effective_model`、`fallback_reason` 和 `queue_wait_ms`。
- [x] 重跑定向测试并确认通过。

### Task 3: 验证与文档

**Files:**
- Modify: `docs/superpowers/specs/2026-07-10-image-subtask-logs-design.md`

- [x] 更新队列回退状态与日志契约。
- [x] 运行相关后端测试、Python 编译检查、前端生产构建和 `git diff --check`。
