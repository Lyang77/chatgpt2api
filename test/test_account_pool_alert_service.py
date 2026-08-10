from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from unittest import mock

from services import account_pool_alert_service as alert_service
from services.account_pool_alert_service import (
    AccountPoolZeroAlertMonitor,
    send_zero_account_feishu_alert,
)


class _FakeResponse:
    def __init__(self, status: int, payload: object) -> None:
        self.status = status
        self._payload = payload

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        return None

    def getcode(self) -> int:
        return self.status

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


class _RawResponse(_FakeResponse):
    def __init__(self, status: int, body: bytes) -> None:
        super().__init__(status, None)
        self._body = body

    def read(self) -> bytes:
        return self._body


class AccountPoolAlertCardTests(unittest.TestCase):
    def test_build_card_contains_only_zero_alert_and_beijing_time(self) -> None:
        self.assertTrue(
            hasattr(alert_service, "build_zero_account_alert_card"),
            "build_zero_account_alert_card 尚未实现",
        )
        if not hasattr(alert_service, "build_zero_account_alert_card"):
            return

        checked_at = datetime(2026, 8, 10, 3, 4, 5, tzinfo=timezone.utc)
        card = alert_service.build_zero_account_alert_card(
            {
                "active": 0,
                "total": 7,
                "limited": 2,
                "abnormal": 3,
                "disabled": 2,
            },
            checked_at=checked_at,
        )

        self.assertEqual(card["schema"], "2.0")
        self.assertEqual(card["header"]["template"], "red")
        self.assertEqual(card["header"]["title"]["content"], "chatgpt2api 可用账号告警")
        self.assertEqual(len(card["body"]["elements"]), 2)
        markdown = "\n".join(
            element["content"]
            for element in card["body"]["elements"]
            if element.get("tag") == "markdown"
        )
        self.assertIn("可用账号为 0", markdown)
        self.assertIn("请及时补充或检查账号", markdown)
        self.assertIn("2026-08-10 11:04:05", markdown)
        self.assertNotIn("总账号", markdown)
        self.assertNotIn("限流", markdown)
        self.assertNotIn("异常", markdown)
        self.assertNotIn("禁用", markdown)


class AccountPoolAlertSenderTests(unittest.TestCase):
    def test_send_posts_feishu_interactive_card_payload(self) -> None:
        response = _FakeResponse(200, {"code": 0, "msg": "success", "data": {}})
        opener = mock.Mock(return_value=response)

        sent = send_zero_account_feishu_alert(
            {"active": 0, "total": 0, "limited": 0, "abnormal": 0, "disabled": 0},
            urlopen_fn=opener,
        )

        self.assertTrue(sent)
        request = opener.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["msg_type"], "interactive")
        self.assertEqual(payload["card"]["header"]["template"], "red")
        self.assertEqual(payload["card"]["header"]["title"]["content"], "chatgpt2api 可用账号告警")
        self.assertEqual(opener.call_args.kwargs["timeout"], 5)

    def test_send_returns_false_for_http_failure(self) -> None:
        opener = mock.Mock(return_value=_FakeResponse(500, {"code": 0, "msg": "success"}))

        sent = send_zero_account_feishu_alert(
            {"active": 0},
            urlopen_fn=opener,
        )

        self.assertFalse(sent)

    def test_send_returns_false_for_feishu_business_failure(self) -> None:
        opener = mock.Mock(return_value=_FakeResponse(200, {"code": 19001, "msg": "invalid"}))

        sent = send_zero_account_feishu_alert(
            {"active": 0},
            urlopen_fn=opener,
        )

        self.assertFalse(sent)

    def test_send_returns_false_for_invalid_json_response(self) -> None:
        opener = mock.Mock(return_value=_RawResponse(200, b"not-json"))

        sent = send_zero_account_feishu_alert(
            {"active": 0},
            urlopen_fn=opener,
        )

        self.assertFalse(sent)

    def test_send_returns_false_for_network_error(self) -> None:
        opener = mock.Mock(side_effect=OSError("network unavailable"))

        try:
            sent = send_zero_account_feishu_alert(
                {"active": 0},
                urlopen_fn=opener,
            )
        except Exception as exc:
            self.fail(f"Webhook 网络异常不应逃出发送函数：{exc}")

        self.assertFalse(sent)


class AccountPoolZeroAlertMonitorTests(unittest.TestCase):
    def test_continuous_zero_sends_only_once(self) -> None:
        sender = mock.Mock(return_value=True)
        monitor = AccountPoolZeroAlertMonitor(sender)

        self.assertTrue(monitor.check({"active": 0}))
        self.assertFalse(monitor.check({"active": 0}))
        sender.assert_called_once_with({"active": 0})

    def test_positive_account_count_does_not_send(self) -> None:
        sender = mock.Mock(return_value=True)
        monitor = AccountPoolZeroAlertMonitor(sender)

        self.assertFalse(monitor.check({"active": 2}))
        sender.assert_not_called()

    def test_recovery_resets_without_sending_and_next_zero_sends_again(self) -> None:
        sender = mock.Mock(return_value=True)
        monitor = AccountPoolZeroAlertMonitor(sender)

        self.assertTrue(monitor.check({"active": 0}))
        self.assertFalse(monitor.check({"active": 2}))
        self.assertTrue(monitor.check({"active": 0}))
        self.assertEqual(sender.call_count, 2)

    def test_failed_send_is_retried_on_next_zero_check(self) -> None:
        sender = mock.Mock(side_effect=[False, True])
        monitor = AccountPoolZeroAlertMonitor(sender)

        self.assertFalse(monitor.check({"active": 0}))
        self.assertTrue(monitor.check({"active": 0}))
        self.assertEqual(sender.call_count, 2)


if __name__ == "__main__":
    unittest.main()
