from __future__ import annotations

import importlib
import unittest
from unittest import mock


class AccountWatcherAlertTests(unittest.TestCase):
    def _support_module(self):
        support = importlib.import_module("api.support")
        self.assertTrue(
            hasattr(support, "run_limited_account_watcher_cycle"),
            "run_limited_account_watcher_cycle 尚未实现",
        )
        return support

    def test_cycle_refreshes_accounts_then_checks_latest_stats(self) -> None:
        support = self._support_module()
        if not hasattr(support, "run_limited_account_watcher_cycle"):
            return

        events: list[str] = []
        stats = {"active": 0, "total": 2}
        service = mock.Mock()
        service.list_limited_tokens.return_value = ["limited"]
        service.list_normal_tokens.return_value = ["normal"]
        service.list_expiring_access_tokens.return_value = []
        service.list_refresh_token_keepalive_tokens.return_value = []
        service.refresh_accounts.side_effect = lambda _tokens: events.append("refresh")
        service.get_stats.side_effect = lambda: events.append("stats") or stats
        monitor = mock.Mock()
        monitor.check.side_effect = lambda _stats: events.append("alert")

        with (
            mock.patch.object(support, "account_service", service),
            mock.patch.object(support, "account_pool_zero_alert_monitor", monitor),
        ):
            support.run_limited_account_watcher_cycle()

        service.refresh_accounts.assert_called_once_with(["limited", "normal"])
        monitor.check.assert_called_once_with(stats)
        self.assertEqual(events, ["refresh", "stats", "alert"])

    def test_cycle_still_checks_alert_when_refresh_fails(self) -> None:
        support = self._support_module()
        if not hasattr(support, "run_limited_account_watcher_cycle"):
            return

        stats = {"active": 0, "total": 1}
        service = mock.Mock()
        service.list_limited_tokens.return_value = []
        service.list_normal_tokens.return_value = ["normal"]
        service.list_expiring_access_tokens.return_value = []
        service.list_refresh_token_keepalive_tokens.return_value = []
        service.refresh_accounts.side_effect = RuntimeError("refresh failed")
        service.get_stats.return_value = stats
        monitor = mock.Mock()

        with (
            mock.patch.object(support, "account_service", service),
            mock.patch.object(support, "account_pool_zero_alert_monitor", monitor),
            self.assertRaisesRegex(RuntimeError, "refresh failed"),
        ):
            support.run_limited_account_watcher_cycle()

        monitor.check.assert_called_once_with(stats)

    def test_empty_pool_checks_alert_without_refreshing_accounts(self) -> None:
        support = self._support_module()
        if not hasattr(support, "run_limited_account_watcher_cycle"):
            return

        stats = {"active": 0, "total": 0}
        service = mock.Mock()
        service.list_limited_tokens.return_value = []
        service.list_normal_tokens.return_value = []
        service.list_expiring_access_tokens.return_value = []
        service.list_refresh_token_keepalive_tokens.return_value = []
        service.get_stats.return_value = stats
        monitor = mock.Mock()

        with (
            mock.patch.object(support, "account_service", service),
            mock.patch.object(support, "account_pool_zero_alert_monitor", monitor),
        ):
            support.run_limited_account_watcher_cycle()

        service.refresh_accounts.assert_not_called()
        monitor.check.assert_called_once_with(stats)


if __name__ == "__main__":
    unittest.main()
