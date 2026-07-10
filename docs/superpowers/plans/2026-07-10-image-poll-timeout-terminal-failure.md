# 图片轮询超时直接失败实施计划

> **给 agentic workers：** 必须使用 `superpowers:executing-plans` 按任务逐步执行本计划。步骤使用 checkbox（`- [ ]`）语法跟踪。

**目标：** 将 `image_poll_timeout_secs` 设为图片结果轮询的唯一预算；耗尽后当前图片直接失败。

**架构：** 仅收紧图片轮询超时路径。移除文本/后备路径的 300 秒下限，并阻止 `ImagePollTimeoutError` 在账号池中换号重试；不改变网络/TLS 重试和日志计时语义。

**技术栈：** Python、`unittest`、`unittest.mock`。

## 全局约束

- 不修改用户现有的图片子任务日志改动。
- 不新增配置项；继续使用 `config.image_poll_timeout_secs`。
- 用测试先行证明轮询超时不会再次占用账号。

---

### 任务 1：锁定轮询预算与终止语义

**文件：**
- 修改：`test/test_multi_image_results.py`
- 修改：`services/protocol/conversation.py:923-926,1012,1117,1415-1432`

**接口：**
- 使用：`stream_image_outputs()`、`_generate_single_image()`、`ImagePollTimeoutError`。
- 产出：所有图片结果轮询调用的超时参数等于 `config.image_poll_timeout_secs`；超时异常不进行账号池重试。

- [ ] **步骤 1：编写失败测试**

```python
def test_text_reply_poll_uses_configured_timeout(self):
    # Fake backend records the timeout passed to resolve_conversation_image_urls.
    # With image_poll_timeout_secs=17, a text reply must pass 17, not 300.
    self.assertEqual(backend.poll_timeouts, [17])

def test_poll_timeout_does_not_select_another_account(self):
    # The first backend raises ImagePollTimeoutError before yielding output.
    with self.assertRaises(ImagePollTimeoutError):
        _generate_single_image(ConversationRequest(prompt="draw"), 1, 1)
    self.assertEqual(select.call_count, 1)
```

- [ ] **步骤 2：运行失败测试**

运行：`python -m unittest test.test_multi_image_results.MultiImageResultTests.test_text_reply_poll_uses_configured_timeout test.test_multi_image_results.MultiImageResultTests.test_poll_timeout_does_not_select_another_account -v`

预期：第一个测试收到 `300`，第二个测试选择账号超过一次。

- [ ] **步骤 3：实施最小修改**

```python
# 所有异步/文本回复轮询均保留配置预算，不再以 300 秒兜底。
poll_timeout = config.image_poll_timeout_secs
retry_poll_timeout = config.image_poll_timeout_secs

except ImagePollTimeoutError as exc:
    account_service.mark_image_result(token, False)
    if account_email:
        setattr(exc, "account_email", account_email)
    raise
```

- [ ] **步骤 4：运行聚焦测试**

运行：`python -m unittest test.test_multi_image_results -v`

预期：全部通过。

- [ ] **步骤 5：运行静态与差异检查**

运行：`python -m compileall services/protocol/conversation.py test/test_multi_image_results.py` 与 `git diff --check`。

预期：退出码为 0。

- [ ] **步骤 6：提交本次代码和测试**

```bash
git add services/protocol/conversation.py test/test_multi_image_results.py \
  docs/superpowers/plans/2026-07-10-image-poll-timeout-terminal-failure.md
git commit -m "fix: fail image task when poll timeout expires"
```

提交前确认暂存区不包含用户已有的日志功能改动。
