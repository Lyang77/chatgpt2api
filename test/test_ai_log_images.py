from __future__ import annotations

import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.ai as ai_module


AUTH_HEADERS = {"Authorization": "Bearer chatgpt2api"}


class _CapturedCall:
    instances: list["_CapturedCall"] = []

    def __init__(self, *args: object, **kwargs: object) -> None:
        self.args = args
        self.kwargs = kwargs
        self.request_urls = kwargs.get("request_urls")
        self.__class__.instances.append(self)

    async def run(self, _handler: object, *_args: object, **_kwargs: object) -> dict[str, object]:
        return {"ok": True}


class AiLogImageTests(unittest.TestCase):
    def setUp(self) -> None:
        _CapturedCall.instances.clear()
        self.patchers = [
            mock.patch.object(ai_module, "require_identity", return_value={"id": "key-1", "role": "admin"}),
            mock.patch.object(ai_module, "LoggedCall", _CapturedCall),
            mock.patch.object(ai_module, "filter_or_log", new=mock.AsyncMock()),
        ]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        app = FastAPI()
        app.include_router(ai_module.create_router())
        self.client = TestClient(app)

    def test_chat_completion_records_request_image_urls(self) -> None:
        response = self.client.post(
            "/v1/chat/completions",
            headers=AUTH_HEADERS,
            json={
                "model": "gpt-5-5",
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "describe this"},
                        {"type": "image_url", "image_url": {"url": "https://example.test/request.png"}},
                    ],
                }],
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            _CapturedCall.instances[0].kwargs.get("request_urls"),
            ["https://example.test/request.png"],
        )
        self.assertEqual(
            _CapturedCall.instances[0].kwargs.get("request_meta"),
            {
                "message_count": 1,
                "role_counts": {"user": 1},
                "image_input_count": 1,
            },
        )

    def test_responses_records_request_image_urls(self) -> None:
        response = self.client.post(
            "/v1/responses",
            headers=AUTH_HEADERS,
            json={
                "model": "gpt-5-5",
                "input": [{
                    "type": "input_image",
                    "image_url": "https://example.test/request.png",
                }],
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            _CapturedCall.instances[0].kwargs.get("request_urls"),
            ["https://example.test/request.png"],
        )
        self.assertEqual(
            _CapturedCall.instances[0].kwargs.get("request_meta"),
            {"input_item_count": 1, "image_input_count": 1},
        )

    def test_messages_records_request_metadata(self) -> None:
        response = self.client.post(
            "/v1/messages",
            headers=AUTH_HEADERS,
            json={
                "model": "gpt-5-5",
                "system": "system",
                "messages": [{"role": "user", "content": "hello"}],
                "tools": [{"name": "lookup", "input_schema": {"type": "object"}}],
                "max_tokens": 500,
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            _CapturedCall.instances[0].kwargs.get("request_meta"),
            {
                "max_tokens": 500,
                "tool_count": 1,
                "message_count": 1,
                "role_counts": {"user": 1},
                "system_chars": len("system"),
            },
        )

    def test_search_records_request_metadata(self) -> None:
        response = self.client.post(
            "/v1/search",
            headers=AUTH_HEADERS,
            json={"prompt": "find current docs"},
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            _CapturedCall.instances[0].kwargs.get("request_meta"),
            {"prompt_chars": len("find current docs")},
        )

    def test_editable_file_tasks_record_request_metadata(self) -> None:
        with (
            mock.patch.object(ai_module.editable_file_task_service, "submit_ppt", return_value={"id": "deck-1"}),
            mock.patch.object(ai_module.editable_file_task_service, "submit_psd", return_value={"id": "psd-1"}),
        ):
            ppt_response = self.client.post(
                "/v1/ppt/generations",
                headers=AUTH_HEADERS,
                json={
                    "prompt": "make slides",
                    "client_task_id": "deck-1",
                    "base64_images": ["SECRET"],
                },
            )
            psd_response = self.client.post(
                "/v1/psd/generations",
                headers=AUTH_HEADERS,
                json={"prompt": "make psd", "client_task_id": "psd-1"},
            )

        self.assertEqual(ppt_response.status_code, 200, ppt_response.text)
        self.assertEqual(psd_response.status_code, 200, psd_response.text)
        self.assertEqual(
            _CapturedCall.instances[0].kwargs.get("request_meta"),
            {
                "client_task_id": "deck-1",
                "reference_image_count": 1,
                "prompt_chars": len("make slides"),
            },
        )
        self.assertEqual(
            _CapturedCall.instances[1].kwargs.get("request_meta"),
            {
                "client_task_id": "psd-1",
                "reference_image_count": 0,
                "prompt_chars": len("make psd"),
            },
        )

    def test_image_generation_records_request_metadata(self) -> None:
        response = self.client.post(
            "/v1/images/generations",
            headers=AUTH_HEADERS,
            json={
                "prompt": "draw",
                "model": "gpt-image-2",
                "size": "1536x1024",
                "quality": "high",
                "n": 2,
                "output_format": "webp",
                "response_format": "url",
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            _CapturedCall.instances[0].kwargs.get("request_meta"),
            {
                "mode": "generate",
                "size": "1536x1024",
                "quality": "high",
                "n": 2,
                "output_format": "webp",
                "response_format": "url",
            },
        )

    def test_image_edit_records_primary_and_mask_images(self) -> None:
        payload = {"prompt": "edit", "model": "gpt-image-2"}
        primary = [(b"primary", "primary.png", "image/png")]
        mask = [(b"mask", "mask.png", "image/png")]

        with (
            mock.patch.object(ai_module, "parse_image_edit_request", new=mock.AsyncMock(return_value=(payload, ["primary"], ["mask"]))),
            mock.patch.object(ai_module, "read_image_sources", new=mock.AsyncMock(side_effect=[primary, mask])),
            mock.patch.object(
                ai_module,
                "collect_request_image_input_urls",
                side_effect=lambda images, _base_url: [f"http://app.test/{images[0][1]}"],
            ),
        ):
            response = self.client.post("/v1/images/edits", headers=AUTH_HEADERS)

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            _CapturedCall.instances[0].request_urls,
            ["http://app.test/primary.png", "http://app.test/mask.png"],
        )
        self.assertEqual(
            _CapturedCall.instances[0].kwargs.get("request_meta"),
            {
                "mode": "edit",
                "quality": "auto",
                "n": 1,
                "output_format": "png",
                "response_format": "b64_json",
                "reference_image_count": 1,
                "mask_image_count": 1,
            },
        )


if __name__ == "__main__":
    unittest.main()
