from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from services.account_service import AccountModelUnavailableError
from services.log_service import LoggedCall
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
            "get_available_access_token",
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
        self.assertIn("no available account supports model gpt-5-3", response.body.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
