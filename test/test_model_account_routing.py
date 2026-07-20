from __future__ import annotations

import asyncio
import json
import unittest
from unittest import mock

from services.account_service import AccountModelUnavailableError
from services.log_service import LoggedCall
from services.openai_backend_api import _CodexTextUpstreamHTTPError
import services.protocol.conversation as conversation_module
import services.protocol.openai_v1_chat_complete as chat_completion_module
from services.protocol.conversation import ConversationRequest, ImageGenerationError


class _FakeBackend:
    def __init__(self, access_token: str = "") -> None:
        self.access_token = access_token

    def close(self) -> None:
        return None


class ModelAccountRoutingTests(unittest.TestCase):
    def test_text_backend_selects_account_for_requested_model(self) -> None:
        with (
            mock.patch.object(conversation_module.account_service, "get_text_access_token", return_value="token-a") as select,
            mock.patch.object(conversation_module, "OpenAIBackendAPI", _FakeBackend),
        ):
            backend = conversation_module.text_backend("gpt-5-3")

        select.assert_called_once_with("gpt-5-3")
        self.assertEqual(backend.access_token, "token-a")

    def test_chat_completion_passes_requested_model_to_account_selection(self) -> None:
        def collect_selected_account(_backend, request):
            request.account_email = "executor@example.test"
            return "ok"

        with (
            mock.patch.object(chat_completion_module, "text_backend", return_value=_FakeBackend()) as select,
            mock.patch.object(chat_completion_module, "collect_text", side_effect=collect_selected_account),
            mock.patch.object(
                chat_completion_module.chat_completion_cache,
                "get_or_compute_response",
                side_effect=lambda _key, compute: compute(),
            ),
        ):
            response = chat_completion_module.handle({
                "model": "gpt-5-5",
                "messages": [{"role": "user", "content": "hello"}],
            })

        select.assert_called_once_with("gpt-5-5")
        self.assertEqual(response["model"], "gpt-5-5")
        self.assertEqual(response["_account_email"], "executor@example.test")

    def test_stream_chat_completion_preserves_selected_account_for_logs(self) -> None:
        def stream_selected_account(_backend, request):
            request.account_email = "executor@example.test"
            yield "ok"

        with mock.patch.object(chat_completion_module, "stream_text_deltas", side_effect=stream_selected_account):
            chunks = list(chat_completion_module.stream_text_chat_completion(
                _FakeBackend(),
                [{"role": "user", "content": "hello"}],
                "gpt-5-5",
            ))

        self.assertEqual(chunks[0]["_account_email"], "executor@example.test")
        self.assertEqual(chunks[-1]["_account_email"], "executor@example.test")

    def test_text_stream_reads_selected_account_email(self) -> None:
        request = ConversationRequest(model="gpt-5-5", messages=[{"role": "user", "content": "hello"}])
        with (
            mock.patch.object(conversation_module, "OpenAIBackendAPI", _FakeBackend),
            mock.patch.object(
                conversation_module,
                "conversation_events",
                return_value=iter([{"type": "conversation.delta", "delta": "ok"}]),
            ),
            mock.patch.object(
                conversation_module.account_service,
                "get_account",
                return_value={"email": "executor@example.test"},
            ),
            mock.patch.object(conversation_module.account_service, "mark_text_used"),
        ):
            result = list(conversation_module.stream_text_deltas(_FakeBackend("token-a"), request))

        self.assertEqual(result, ["ok"])
        self.assertEqual(request.account_email, "executor@example.test")

    def test_image_pool_selection_receives_requested_model(self) -> None:
        with mock.patch.object(
            conversation_module.account_service,
            "get_available_access_token_with_fallback",
            side_effect=RuntimeError("no matching account"),
        ) as select:
            with self.assertRaises(ImageGenerationError):
                next(conversation_module.stream_image_outputs_with_pool(
                    ConversationRequest(prompt="draw", model="gpt-image-2"),
                ))

        self.assertEqual(select.call_args.kwargs["model"], "gpt-image-2")

    def test_model_allowlist_error_returns_service_unavailable(self) -> None:
        def handler():
            raise AccountModelUnavailableError("gpt-5-3")

        with mock.patch("services.log_service.log_service"):
            response = asyncio.run(
                LoggedCall({"id": "admin", "name": "test", "role": "admin"}, "/v1/chat/completions", "gpt-5-3", "文本生成").run(handler)
            )

        self.assertEqual(response.status_code, 503)
        payload = json.loads(response.body)
        self.assertEqual(payload["error"]["message"], "no available account supports model gpt-5-3")
        self.assertEqual(payload["error"]["type"], "service_unavailable")
        self.assertEqual(payload["error"]["code"], "model_account_unavailable")

    def test_stream_model_allowlist_error_returns_service_unavailable_before_sse(self) -> None:
        def handler():
            def events():
                raise AccountModelUnavailableError("gpt-5.5")
                yield  # pragma: no cover

            return events()

        with mock.patch("services.log_service.log_service"):
            response = asyncio.run(
                LoggedCall(
                    {"id": "admin", "name": "test", "role": "admin"},
                    "/v1/responses",
                    "gpt-5.5",
                    "文本生成",
                ).run(handler)
            )

        self.assertEqual(response.status_code, 503)
        payload = json.loads(response.body)
        self.assertEqual(payload["error"]["type"], "service_unavailable")
        self.assertEqual(payload["error"]["code"], "model_account_unavailable")

    def test_codex_upstream_http_error_preserves_status_body_and_retry_after(self) -> None:
        def handler():
            raise _CodexTextUpstreamHTTPError(
                "/backend-api/codex/responses",
                429,
                {
                    "error": {
                        "message": "rate limited",
                        "type": "rate_limit_error",
                        "code": "rate_limit_exceeded",
                    }
                },
                retry_after=7,
            )

        with mock.patch("services.log_service.log_service"):
            response = asyncio.run(
                LoggedCall(
                    {"id": "admin", "name": "test", "role": "admin"},
                    "/v1/chat/completions",
                    "gpt-5.5",
                    "文本生成",
                ).run(handler)
            )

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["retry-after"], "7")
        payload = json.loads(response.body)
        self.assertEqual(payload["error"]["message"], "rate limited")
        self.assertEqual(payload["error"]["type"], "rate_limit_error")
        self.assertEqual(payload["error"]["code"], "rate_limit_exceeded")

    def test_stream_codex_upstream_http_error_is_mapped_before_sse(self) -> None:
        def handler():
            def events():
                raise _CodexTextUpstreamHTTPError(
                    "/backend-api/codex/responses",
                    401,
                    {"error": {"message": "expired", "type": "authentication_error", "code": "invalid_api_key"}},
                )
                yield  # pragma: no cover

            return events()

        with mock.patch("services.log_service.log_service"):
            response = asyncio.run(
                LoggedCall(
                    {"id": "admin", "name": "test", "role": "admin"},
                    "/v1/responses",
                    "gpt-5.5",
                    "文本生成",
                ).run(handler)
            )

        self.assertEqual(response.status_code, 401)
        payload = json.loads(response.body)
        self.assertEqual(payload["error"]["type"], "authentication_error")
        self.assertEqual(payload["error"]["code"], "invalid_api_key")


if __name__ == "__main__":
    unittest.main()
