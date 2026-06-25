from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import services.log_service as log_module
from services.log_service import LogService, LoggedCall, collect_request_image_urls


IDENTITY = {"id": "key-1", "name": "test-key", "role": "admin"}


class LoggedCallResponseTextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "logs.jsonl"
        self.original_log_service = log_module.log_service
        log_module.log_service = LogService(self.path)

    def tearDown(self) -> None:
        log_module.log_service = self.original_log_service
        self.temp_dir.cleanup()

    def _last_detail(self) -> dict[str, object]:
        item = self._last_item()
        detail = item.get("detail")
        self.assertIsInstance(detail, dict)
        return detail

    def _last_item(self) -> dict[str, object]:
        line = self.path.read_text(encoding="utf-8").splitlines()[-1]
        item = json.loads(line)
        self.assertIsInstance(item, dict)
        return item

    def test_chat_completion_log_records_assistant_text(self) -> None:
        call = LoggedCall(IDENTITY, "/v1/chat/completions", "auto", "文本生成", request_text="say hello")

        call.log("调用完成", {
            "id": "chatcmpl_test",
            "object": "chat.completion",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": "hello from assistant"},
                "finish_reason": "stop",
            }],
        })

        self.assertEqual(self._last_detail().get("response_text"), "hello from assistant")

    def test_responses_log_records_output_text(self) -> None:
        call = LoggedCall(IDENTITY, "/v1/responses", "auto", "Responses", request_text="say hello")

        call.log("调用完成", {
            "id": "resp_test",
            "object": "response",
            "output": [{
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "hello from responses"}],
            }],
        })

        self.assertEqual(self._last_detail().get("response_text"), "hello from responses")

    def test_stream_log_records_text_deltas_without_changing_chunks(self) -> None:
        call = LoggedCall(IDENTITY, "/v1/chat/completions", "auto", "文本生成", request_text="say hello")
        chunks = [
            {"choices": [{"delta": {"role": "assistant", "content": "hello "}, "finish_reason": None}]},
            {"choices": [{"delta": {"content": "from stream"}, "finish_reason": None}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        ]

        emitted = list(call.stream(iter(chunks)))

        self.assertEqual(emitted, chunks)
        self.assertEqual(self._last_detail().get("response_text"), "hello from stream")

    def test_log_list_omits_response_text_but_detail_keeps_it(self) -> None:
        call = LoggedCall(
            IDENTITY,
            "/v1/chat/completions",
            "auto",
            "文本生成",
            request_text="say hello",
            request_urls=["https://example.test/input.png"],
        )
        call.log("调用完成", {
            "choices": [{
                "message": {"role": "assistant", "content": "hello from detail only"},
            }],
        })
        item_id = str(self._last_item()["id"])

        listed_detail = log_module.log_service.list(type="call")["items"][0]["detail"]
        full_detail = log_module.log_service.get_by_id(item_id)["detail"]  # type: ignore[index]

        self.assertNotIn("request_text", listed_detail)
        self.assertNotIn("response_text", listed_detail)
        self.assertNotIn("request_urls", listed_detail)
        self.assertEqual(full_detail.get("request_urls"), ["https://example.test/input.png"])
        self.assertEqual(full_detail.get("response_text"), "hello from detail only")

    def test_logged_call_records_cache_hit_without_exposing_internal_marker(self) -> None:
        call = LoggedCall(IDENTITY, "/v1/chat/completions", "auto", "文本生成", request_text="say hello")

        response = asyncio.run(call.run(lambda: {
            "_cache_hit": True,
            "choices": [{
                "message": {"role": "assistant", "content": "hello from cache"},
            }],
        }))

        self.assertNotIn("_cache_hit", response)
        listed_detail = log_module.log_service.list(type="call")["items"][0]["detail"]
        self.assertIs(listed_detail.get("cache_hit"), True)

    def test_collect_request_image_urls_keeps_remote_urls(self) -> None:
        payload = {
            "messages": [{
                "role": "user",
                "content": [{
                    "type": "image_url",
                    "image_url": {"url": "https://example.test/input.png"},
                }],
            }],
        }

        self.assertEqual(
            collect_request_image_urls(payload, "http://app.test"),
            ["https://example.test/input.png"],
        )

    def test_collect_request_image_urls_saves_data_urls(self) -> None:
        data_url = "data:image/png;base64,ZmFrZS1pbWFnZQ=="
        payload = {"input": [{"type": "input_image", "image_url": data_url}]}
        stored = SimpleNamespace(url="http://app.test/images/request-input.png")

        with mock.patch("services.log_service.image_storage_service.save", return_value=stored) as save:
            urls = collect_request_image_urls(payload, "http://app.test")

        self.assertEqual(urls, ["http://app.test/images/request-input.png"])
        save.assert_called_once_with(b"fake-image", "http://app.test")

    def test_collect_request_image_urls_ignores_storage_failures(self) -> None:
        data_url = "data:image/png;base64,ZmFrZS1pbWFnZQ=="
        payload = {"input": [{"type": "input_image", "image_url": data_url}]}

        with mock.patch("services.log_service.image_storage_service.save", side_effect=RuntimeError("disk full")):
            urls = collect_request_image_urls(payload, "http://app.test")

        self.assertEqual(urls, [])


if __name__ == "__main__":
    unittest.main()
