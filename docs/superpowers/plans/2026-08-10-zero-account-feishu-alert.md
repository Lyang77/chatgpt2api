# 可用账号归零飞书告警实施计划

> **给 agentic workers：** 必须使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans` 按任务逐步执行本计划。步骤使用 checkbox（`- [ ]`）语法跟踪。

**目标：** 在可用账号数为 `0` 时，通过用户提供的飞书群机器人 Webhook 发送一次告警，并在账号恢复后允许下一次归零重新告警。

**架构：** 新增独立的 `account_pool_alert_service`，负责飞书卡片消息、HTTP 发送和进程内去重状态；现有 `account-watcher` 每轮账号维护后调用它。发送发生在后台线程，失败时保留待告警状态并在下个检查周期重试。

**技术栈：** Python 3.13、标准库 `urllib.request`、`unittest`、`unittest.mock`、FastAPI lifespan 后台线程。

## 全局约束

- 可用账号口径固定为 `AccountService.get_stats()["active"]`。
- 首次检查已经为 `0` 时也必须告警。
- 连续为 `0` 只发送一次；恢复时不发送消息，只重置进程内状态。
- Webhook 地址按用户要求只在生产源码常量中写死，不新增配置项，也不在测试和计划中重复保存完整凭据。
- 卡片只显示可用账号归零告警、处理提示和北京时间，不展示号池数量明细。
- Webhook 请求超时固定为 `5` 秒，不允许异常逃出发送函数。
- 不引入新依赖，不增加任务表、队列、持久化状态或前端配置。
- 自动化测试通过可替换的 opener 验证 HTTP 行为；真实 Webhook 只在用户明确要求联调时手动发送。
- 用户未授权 Git 提交，本计划执行期间不得创建分支、提交或推送。

---

## 文件结构

- 新建 `services/account_pool_alert_service.py`：保存 Webhook 源码常量，构造极简告警卡片，发送飞书请求，维护一次连续归零状态的去重逻辑。
- 新建 `test/test_account_pool_alert_service.py`：覆盖消息内容、HTTP 成败和归零状态变化。
- 修改 `api/support.py:82`：提取一轮账号维护函数，并在每轮结束后执行告警检查。
- 新建 `test/test_account_watcher_alert.py`：验证账号刷新后使用最新统计检查告警，并验证维护失败时仍执行检查。

### 任务 1：飞书卡片消息与 Webhook 发送器

**文件：**
- 新建：`test/test_account_pool_alert_service.py`
- 新建：`services/account_pool_alert_service.py`

**接口：**
- 产出：`build_zero_account_alert_card(stats: Mapping[str, object], checked_at: datetime | None = None) -> dict[str, object]`
- 产出：`send_zero_account_feishu_alert(stats: Mapping[str, object], *, checked_at: datetime | None = None, urlopen_fn: Callable[..., object] | None = None) -> bool`

- [x] **步骤 1：先写消息和成功发送测试**

```python
class AccountPoolAlertSenderTests(unittest.TestCase):
    def test_build_card_contains_only_zero_alert_and_beijing_time(self) -> None:
        checked_at = datetime(2026, 8, 10, 3, 4, 5, tzinfo=timezone.utc)
        card = build_zero_account_alert_card(
            {"active": 0, "total": 7, "limited": 2, "abnormal": 3, "disabled": 2},
            checked_at=checked_at,
        )
        markdown = "\n".join(element["content"] for element in card["body"]["elements"])
        self.assertEqual(card["header"]["template"], "red")
        self.assertIn("可用账号为 0", markdown)
        self.assertIn("2026-08-10 11:04:05", markdown)
        self.assertNotIn("总账号", markdown)

    def test_send_posts_feishu_interactive_card_payload(self) -> None:
        response = _FakeResponse(200, {"code": 0, "msg": "success"})
        opener = mock.Mock(return_value=response)
        sent = send_zero_account_feishu_alert(
            {"active": 0, "total": 0, "limited": 0, "abnormal": 0, "disabled": 0},
            urlopen_fn=opener,
        )
        self.assertTrue(sent)
        request = opener.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["msg_type"], "interactive")
        self.assertEqual(payload["card"]["schema"], "2.0")
        self.assertEqual(opener.call_args.kwargs["timeout"], 5)
```

- [x] **步骤 2：运行测试并确认因模块尚不存在而失败**

运行：`python -m unittest test.test_account_pool_alert_service.AccountPoolAlertSenderTests -v`

预期：`ModuleNotFoundError: No module named 'services.account_pool_alert_service'`。

- [x] **步骤 3：实现最小发送逻辑**

在 `services/account_pool_alert_service.py` 中：

```python
BEIJING_TIMEZONE = timezone(timedelta(hours=8))
FEISHU_WEBHOOK_TIMEOUT_SECONDS = 5

def build_zero_account_alert_card(stats, checked_at=None):
    checked = (checked_at or datetime.now(BEIJING_TIMEZONE)).astimezone(BEIJING_TIMEZONE)
    active = int(stats.get("active") or 0)
    return {
        "schema": "2.0",
        "header": {
            "title": {"tag": "plain_text", "content": "chatgpt2api 可用账号告警"},
            "template": "red",
        },
        "body": {"elements": [
            {"tag": "markdown", "content": f"🚨 **可用账号为 {active}**\n\n请及时补充或检查账号。"},
            {"tag": "markdown", "content": f"检查时间：{checked:%Y-%m-%d %H:%M:%S}（北京时间）"},
        ]},
    }

def send_zero_account_feishu_alert(stats, *, checked_at=None, urlopen_fn=None):
    opener = urlopen_fn or urllib.request.urlopen
    payload = {"msg_type": "interactive", "card": build_zero_account_alert_card(stats, checked_at)}
    request = urllib.request.Request(
        FEISHU_ACCOUNT_POOL_WEBHOOK_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with opener(request, timeout=FEISHU_WEBHOOK_TIMEOUT_SECONDS) as response:
            status = int(getattr(response, "status", 0) or response.getcode())
            body = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        print(f"[account-alert] feishu send failed: {exc}")
        return False
    code = body.get("code", body.get("StatusCode")) if isinstance(body, dict) else None
    if not 200 <= status < 300 or code not in {0, "0"}:
        print(f"[account-alert] feishu rejected message: http={status}, code={code}")
        return False
    print("[account-alert] feishu zero-account alert sent")
    return True
```

在该模块顶部把用户提供的完整 Webhook 地址赋给 `FEISHU_ACCOUNT_POOL_WEBHOOK_URL`。完整凭据只出现这一次。

- [x] **步骤 4：补充失败分支测试**

分别断言 HTTP 非 2xx、飞书 `code != 0`、无效 JSON 和 opener 抛出 `OSError` 时返回 `False`，且测试中不发真实网络请求。

- [x] **步骤 5：运行发送器测试并确认通过**

运行：`python -m unittest test.test_account_pool_alert_service.AccountPoolAlertSenderTests -v`

预期：发送器测试全部 `ok`，无真实飞书消息。

### 任务 2：连续归零状态去重

**文件：**
- 修改：`test/test_account_pool_alert_service.py`
- 修改：`services/account_pool_alert_service.py`

**接口：**
- 使用：`send_zero_account_feishu_alert(stats) -> bool`
- 产出：`AccountPoolZeroAlertMonitor.check(stats: Mapping[str, object]) -> bool`
- 产出：模块单例 `account_pool_zero_alert_monitor`

- [x] **步骤 1：先写归零状态转换测试**

```python
class AccountPoolZeroAlertMonitorTests(unittest.TestCase):
    def test_continuous_zero_sends_only_once(self) -> None:
        sender = mock.Mock(return_value=True)
        monitor = AccountPoolZeroAlertMonitor(sender)
        self.assertTrue(monitor.check({"active": 0}))
        self.assertFalse(monitor.check({"active": 0}))
        sender.assert_called_once()

    def test_recovery_resets_without_sending_and_next_zero_sends_again(self) -> None:
        sender = mock.Mock(return_value=True)
        monitor = AccountPoolZeroAlertMonitor(sender)
        monitor.check({"active": 0})
        self.assertFalse(monitor.check({"active": 2}))
        self.assertTrue(monitor.check({"active": 0}))
        self.assertEqual(sender.call_count, 2)

    def test_failed_send_is_retried_on_next_zero_check(self) -> None:
        sender = mock.Mock(side_effect=[False, True])
        monitor = AccountPoolZeroAlertMonitor(sender)
        self.assertFalse(monitor.check({"active": 0}))
        self.assertTrue(monitor.check({"active": 0}))
        self.assertEqual(sender.call_count, 2)
```

- [x] **步骤 2：运行测试并确认因类尚不存在而失败**

运行：`python -m unittest test.test_account_pool_alert_service.AccountPoolZeroAlertMonitorTests -v`

预期：导入或属性错误指向 `AccountPoolZeroAlertMonitor` 尚未实现。

- [x] **步骤 3：实现最小状态机**

```python
class AccountPoolZeroAlertMonitor:
    def __init__(self, send_alert=send_zero_account_feishu_alert):
        self._send_alert = send_alert
        self._alert_sent = False

    def check(self, stats):
        if int(stats.get("active") or 0) > 0:
            self._alert_sent = False
            return False
        if self._alert_sent:
            return False
        sent = self._send_alert(stats)
        if sent:
            self._alert_sent = True
        return sent

account_pool_zero_alert_monitor = AccountPoolZeroAlertMonitor()
```

- [x] **步骤 4：运行整个模块测试并确认通过**

运行：`python -m unittest test.test_account_pool_alert_service -v`

预期：所有发送器和状态转换测试均为 `ok`。

### 任务 3：接入现有 account-watcher

**文件：**
- 修改：`api/support.py:82-114`
- 新建：`test/test_account_watcher_alert.py`

**接口：**
- 使用：`account_pool_zero_alert_monitor.check(stats) -> bool`
- 产出：`run_limited_account_watcher_cycle() -> None`
- 保持：`start_limited_account_watcher(stop_event: Event) -> Thread`

- [x] **步骤 1：先写单轮监控集成测试**

```python
class AccountWatcherAlertTests(unittest.TestCase):
    def test_cycle_refreshes_accounts_then_checks_latest_stats(self) -> None:
        service = mock.Mock()
        service.list_limited_tokens.return_value = ["limited"]
        service.list_normal_tokens.return_value = ["normal"]
        service.list_expiring_access_tokens.return_value = []
        service.list_refresh_token_keepalive_tokens.return_value = []
        service.get_stats.return_value = {"active": 0, "total": 2}
        monitor = mock.Mock()
        with (
            mock.patch.object(support, "account_service", service),
            mock.patch.object(support, "account_pool_zero_alert_monitor", monitor),
        ):
            support.run_limited_account_watcher_cycle()
        service.refresh_accounts.assert_called_once_with(["limited", "normal"])
        monitor.check.assert_called_once_with({"active": 0, "total": 2})

    def test_cycle_still_checks_alert_when_refresh_fails(self) -> None:
        service = mock.Mock()
        service.list_limited_tokens.return_value = []
        service.list_normal_tokens.return_value = ["normal"]
        service.list_expiring_access_tokens.return_value = []
        service.list_refresh_token_keepalive_tokens.return_value = []
        service.refresh_accounts.side_effect = RuntimeError("refresh failed")
        service.get_stats.return_value = {"active": 0}
        monitor = mock.Mock()
        with (
            mock.patch.object(support, "account_service", service),
            mock.patch.object(support, "account_pool_zero_alert_monitor", monitor),
            self.assertRaisesRegex(RuntimeError, "refresh failed"),
        ):
            support.run_limited_account_watcher_cycle()
        monitor.check.assert_called_once_with({"active": 0})
```

- [x] **步骤 2：运行测试并确认因单轮函数尚不存在而失败**

运行：`python -m unittest test.test_account_watcher_alert -v`

预期：`AttributeError` 指向 `run_limited_account_watcher_cycle` 尚未实现。

- [x] **步骤 3：提取单轮逻辑并接入告警**

```python
def run_limited_account_watcher_cycle() -> None:
    try:
        limited_tokens = account_service.list_limited_tokens()
        normal_tokens = account_service.list_normal_tokens()
        expiring_tokens = account_service.list_expiring_access_tokens()
        keepalive_tokens = account_service.list_refresh_token_keepalive_tokens()
        tokens = list(dict.fromkeys([*limited_tokens, *normal_tokens, *expiring_tokens]))
        expiring_token_set = set(expiring_tokens)
        keepalive_tokens = [token for token in keepalive_tokens if token not in expiring_token_set]
        if tokens:
            print(
                "[account-watcher] checking "
                f"{len(limited_tokens)} limited accounts, "
                f"{len(normal_tokens)} normal accounts, "
                f"{len(expiring_tokens)} expiring access tokens"
            )
            account_service.refresh_accounts(tokens)
        if keepalive_tokens:
            print(f"[account-watcher] keepalive {len(keepalive_tokens)} refresh tokens")
            result = account_service.keepalive_refresh_tokens(keepalive_tokens)
            if result.get("errors"):
                print(f"[account-watcher] keepalive errors: {result['errors']}")
    finally:
        account_pool_zero_alert_monitor.check(account_service.get_stats())

def start_limited_account_watcher(stop_event: Event) -> Thread:
    interval_seconds = config.refresh_account_interval_minute * 60
    def worker() -> None:
        while not stop_event.is_set():
            try:
                run_limited_account_watcher_cycle()
            except Exception as exc:
                print(f"[account-watcher] fail {exc}")
            stop_event.wait(interval_seconds)
    thread = Thread(target=worker, name="account-watcher", daemon=True)
    thread.start()
    return thread
```

- [x] **步骤 4：运行监控集成测试并确认通过**

运行：`python -m unittest test.test_account_watcher_alert -v`

预期：集成测试全部 `ok`。

### 任务 4：回归验证与安全检查

**文件：**
- 检查：`services/account_pool_alert_service.py`
- 检查：`api/support.py`
- 检查：`test/test_account_pool_alert_service.py`
- 检查：`test/test_account_watcher_alert.py`

- [x] **步骤 1：运行功能相关测试**

运行：`python -m unittest test.test_account_pool_alert_service test.test_account_watcher_alert -v`

预期：全部通过，且输出中没有完整 Webhook 地址。

- [x] **步骤 2：运行完整 Python 测试套件**

运行：`python -m unittest discover -t . -s test -p "test_*.py" -v`

预期：所有测试通过；若存在与本次无关的历史失败，记录准确的失败名称和输出，不把局部通过表述成全量通过。

- [x] **步骤 3：检查语法、差异和敏感地址范围**

运行：

```powershell
python -m compileall services api test
git diff --check
git diff --stat
rg -l 'https://open\.feishu\.cn/open-apis/bot/v2/hook/[0-9a-f-]{36}' .
```

预期：编译和 `git diff --check` 成功；Webhook 标识只出现在 `services/account_pool_alert_service.py`，不出现在测试、日志、设计文档或其他源码中。

- [x] **步骤 4：审阅最终差异，不提交 Git**

确认只包含设计、计划、告警服务、监控接入和对应测试。不得调用真实 Webhook，不创建 commit，不 push。
