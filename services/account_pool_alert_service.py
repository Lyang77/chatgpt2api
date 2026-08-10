from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Callable, Mapping


BEIJING_TIMEZONE = timezone(timedelta(hours=8))
FEISHU_WEBHOOK_TIMEOUT_SECONDS = 5
FEISHU_ACCOUNT_POOL_WEBHOOK_URL = (
    "https://open.feishu.cn/open-apis/bot/v2/hook/9a852d6c-48d9-4431-8a98-b4b0c5a9ffb7"
)


def build_zero_account_alert_card(
    stats: Mapping[str, object],
    checked_at: datetime | None = None,
) -> dict[str, object]:
    checked = (checked_at or datetime.now(BEIJING_TIMEZONE)).astimezone(BEIJING_TIMEZONE)
    active = int(stats.get("active") or 0)
    return {
        "schema": "2.0",
        "config": {"update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": "chatgpt2api 可用账号告警"},
            "template": "red",
            "padding": "12px 12px 12px 12px",
        },
        "body": {
            "direction": "vertical",
            "padding": "12px 12px 12px 12px",
            "elements": [
                {
                    "tag": "markdown",
                    "content": (
                        f"🚨 **可用账号为 {active}**\n\n"
                        "请及时补充或检查账号。"
                    ),
                    "text_align": "left",
                    "text_size": "normal_v2",
                    "margin": "0px 0px 8px 0px",
                },
                {
                    "tag": "markdown",
                    "content": f"检查时间：{checked:%Y-%m-%d %H:%M:%S}（北京时间）",
                    "text_align": "left",
                    "text_size": "notation",
                    "margin": "0px 0px 0px 0px",
                },
            ],
        },
    }


def send_zero_account_feishu_alert(
    stats: Mapping[str, object],
    *,
    checked_at: datetime | None = None,
    urlopen_fn: Callable[..., object] | None = None,
) -> bool:
    payload = {
        "msg_type": "interactive",
        "card": build_zero_account_alert_card(stats, checked_at),
    }
    request = urllib.request.Request(
        FEISHU_ACCOUNT_POOL_WEBHOOK_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    opener = urlopen_fn or urllib.request.urlopen
    try:
        with opener(request, timeout=FEISHU_WEBHOOK_TIMEOUT_SECONDS) as response:
            raw_status = getattr(response, "status", None)
            status = int(raw_status if raw_status is not None else response.getcode())
            result = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        print(f"[account-alert] feishu send failed: {type(exc).__name__}")
        return False

    code = result.get("code", result.get("StatusCode")) if isinstance(result, dict) else None
    if not 200 <= status < 300 or code not in {0, "0"}:
        print(f"[account-alert] feishu rejected message: http={status}, code={code}")
        return False

    print("[account-alert] feishu zero-account alert sent")
    return True


class AccountPoolZeroAlertMonitor:
    def __init__(
        self,
        send_alert: Callable[[Mapping[str, object]], bool] = send_zero_account_feishu_alert,
    ) -> None:
        self._send_alert = send_alert
        self._alert_sent = False

    def check(self, stats: Mapping[str, object]) -> bool:
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
