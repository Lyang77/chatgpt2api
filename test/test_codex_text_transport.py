from __future__ import annotations

import json
import io
import unittest
import urllib.error
from unittest import mock

from fastapi import HTTPException

from services.openai_backend_api import OpenAIBackendAPI
from services.protocol import codex_text
from services.protocol.codex_text import codex_messages
from utils.helper import UpstreamHTTPError


class CodexTextInputTests(unittest.TestCase):
    def test_merges_instructions_and_preserves_multimodal_order(self) -> None:
        instructions, input_items = codex_messages([
            {"role": "system", "content": "system rule"},
            {"role": "developer", "content": "developer rule"},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "template"},
                    {"type": "image_url", "image_url": {"url": "https://example.test/a.png"}},
                    {"type": "input_image", "image_url": "data:image/png;base64,AAAA"},
                ],
            },
        ])

        self.assertEqual(instructions, "system rule\n\ndeveloper rule")
        self.assertEqual(input_items[0]["role"], "user")
        self.assertEqual(
            [part["type"] for part in input_items[0]["content"]],
            ["input_text", "input_image", "input_image"],
        )
        self.assertEqual(input_items[0]["content"][0]["text"], "template")
        self.assertEqual(input_items[0]["content"][1]["image_url"], "https://example.test/a.png")
        self.assertEqual(input_items[0]["content"][2]["image_url"], "data:image/png;base64,AAAA")

    def test_explicit_instructions_precede_message_instructions(self) -> None:
        instructions, input_items = codex_messages(
            [
                {"role": "system", "content": [{"type": "input_text", "text": "system rule"}]},
                {"role": "assistant", "content": [{"type": "text", "text": "prior answer"}]},
                {"role": "user", "content": "next question"},
            ],
            instructions="explicit rule",
        )

        self.assertEqual(instructions, "explicit rule\n\nsystem rule")
        self.assertEqual([item["role"] for item in input_items], ["assistant", "user"])
        self.assertEqual(input_items[0]["content"], [{"type": "output_text", "text": "prior answer"}])
        self.assertEqual(input_items[1]["content"], [{"type": "input_text", "text": "next question"}])

    def test_rejects_file_id_images(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            codex_messages([
                {
                    "role": "user",
                    "content": [{"type": "input_image", "image_url": {"file_id": "file-123"}}],
                }
            ])

        self.assertEqual(raised.exception.status_code, 400)

    def test_rejects_unsupported_image_protocol(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            codex_messages([
                {
                    "role": "user",
                    "content": [{"type": "image_url", "image_url": {"url": "ftp://example.test/a.png"}}],
                }
            ])

        self.assertEqual(raised.exception.status_code, 400)

    def test_rejects_empty_image_url(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            codex_messages([
                {"role": "user", "content": [{"type": "input_image", "image_url": ""}]}
            ])

        self.assertEqual(raised.exception.status_code, 400)


class CodexTextTransportTests(unittest.TestCase):
    @staticmethod
    def _request() -> codex_text.CodexTextRequest:
        return codex_text.CodexTextRequest(
            model="gpt-5.5",
            instructions="system rule",
            input_items=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "template"},
                        {"type": "input_image", "image_url": "data:image/png;base64,AAAA"},
                    ],
                }
            ],
        )

    def _collect_events(self, events: list[dict]) -> list[str]:
        backend = mock.Mock()
        backend.iter_codex_text_response_events.return_value = iter(events)
        with (
            mock.patch.object(codex_text, "OpenAIBackendAPI", return_value=backend),
            mock.patch.object(codex_text.account_service, "get_text_access_token", return_value="token-a"),
            mock.patch.object(codex_text.account_service, "get_account", return_value={"email": "a@example.test"}),
            mock.patch.object(codex_text.account_service, "mark_text_used"),
        ):
            return list(codex_text.stream_codex_text_deltas(self._request()))

    def test_backend_posts_codex_text_payload_without_image_tools(self) -> None:
        backend = OpenAIBackendAPI.__new__(OpenAIBackendAPI)
        backend.access_token = "secret-token"
        backend.base_url = "https://chatgpt.com"
        backend._ensure_codex_source_account = mock.Mock()
        backend._codex_responses_headers = mock.Mock(return_value={"Authorization": "Bearer secret-token"})
        backend._iter_codex_text_response_events = mock.Mock(return_value=iter(()))
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.__exit__.return_value = False

        with (
            mock.patch("services.openai_backend_api.urllib.request.urlopen", return_value=response) as urlopen,
            mock.patch("services.openai_backend_api.account_service.get_account", return_value={"email": "a@example.test", "source_type": "codex"}),
            mock.patch("services.openai_backend_api.logger.info") as log_info,
        ):
            list(
                backend.iter_codex_text_response_events(
                    instructions="system rule",
                    input_items=self._request().input_items,
                )
            )

        outgoing = urlopen.call_args.args[0]
        payload = json.loads(outgoing.data.decode("utf-8"))
        self.assertEqual(outgoing.full_url, "https://chatgpt.com/backend-api/codex/responses")
        self.assertEqual(payload["model"], "gpt-5.5")
        self.assertEqual(payload["reasoning"], {"effort": "low"})
        self.assertEqual(payload["instructions"], "system rule")
        self.assertEqual(payload["input"], self._request().input_items)
        self.assertFalse(payload["store"])
        self.assertTrue(payload["stream"])
        self.assertNotIn("tools", payload)
        self.assertNotIn("tool_choice", payload)
        serialized_log = json.dumps([call.args for call in log_info.call_args_list], default=str)
        self.assertNotIn("secret-token", serialized_log)
        self.assertNotIn("base64,AAAA", serialized_log)

    def test_codex_text_sse_yields_first_event_before_eof(self) -> None:
        class IncrementalRaw:
            headers = {"content-type": "text/event-stream"}
            status = 200

            def __init__(self) -> None:
                self.lines = iter([
                    b'data: {"type":"response.output_text.delta","delta":"first"}\n',
                    b"\n",
                ])
                self.line_reads = 0

            def __iter__(self):
                return self

            def __next__(self):
                self.line_reads += 1
                if self.line_reads > 2:
                    raise AssertionError("parser read past the first SSE event before yielding")
                return next(self.lines)

            def read(self):
                raise AssertionError("Codex text SSE must not use buffered read()")

        raw = IncrementalRaw()
        events = OpenAIBackendAPI._iter_codex_text_response_events(raw, image_count=0)

        self.assertEqual(next(events), {"type": "response.output_text.delta", "delta": "first"})
        self.assertEqual(raw.line_reads, 2)

    def test_codex_text_event_logs_exclude_raw_text_and_reasoning(self) -> None:
        sensitive_delta = "SECRET_USER_PROMPT"
        sensitive_reasoning = "SECRET_REASONING_CONTENT"

        class Raw:
            headers = {"content-type": "text/event-stream"}
            status = 200

            def __iter__(self):
                events = [
                    {"type": "response.output_text.delta", "delta": sensitive_delta},
                    {"type": "response.reasoning.delta", "delta": sensitive_reasoning},
                    {
                        "type": "response.completed",
                        "response": {
                            "status": "completed",
                            "output": [{"type": "output_text", "text": sensitive_delta}],
                        },
                    },
                ]
                for event in events:
                    yield f"data: {json.dumps(event)}\n".encode()
                    yield b"\n"

            def read(self):
                raise AssertionError("Codex text SSE must not use buffered read()")

        with mock.patch("services.openai_backend_api.logger.info") as log_info:
            events = list(OpenAIBackendAPI._iter_codex_text_response_events(Raw(), image_count=2))

        self.assertEqual(len(events), 3)
        serialized = json.dumps([call.args for call in log_info.call_args_list], default=str)
        self.assertNotIn(sensitive_delta, serialized)
        self.assertNotIn(sensitive_reasoning, serialized)
        self.assertNotIn("delta_preview", serialized)
        self.assertNotIn("event_previews", serialized)
        self.assertNotIn("body_preview", serialized)
        self.assertIn("response.output_text.delta", serialized)
        self.assertIn("completed", serialized)
        self.assertIn('"image_input_count": 2', serialized)

    def test_codex_text_http_error_preserves_body_but_has_safe_log_message(self) -> None:
        backend = OpenAIBackendAPI.__new__(OpenAIBackendAPI)
        backend.access_token = "secret-token"
        backend.base_url = "https://chatgpt.com"
        backend._ensure_codex_source_account = mock.Mock()
        backend._codex_responses_headers = mock.Mock(return_value={"Authorization": "Bearer secret-token"})
        sensitive_prompt = "SECRET_HTTP_USER_PROMPT"
        sensitive_reasoning = "SECRET_HTTP_REASONING"
        response_body = {
            "error": {
                "message": sensitive_prompt,
                "reasoning": sensitive_reasoning,
            }
        }
        error = urllib.error.HTTPError(
            "https://chatgpt.com/backend-api/codex/responses",
            500,
            "upstream failure",
            {},
            io.BytesIO(json.dumps(response_body).encode()),
        )

        with (
            mock.patch("services.openai_backend_api.urllib.request.urlopen", side_effect=error),
            mock.patch("services.openai_backend_api.logger.warning") as warning,
        ):
            with self.assertRaises(UpstreamHTTPError) as raised:
                list(backend.iter_codex_text_response_events("system", self._request().input_items))
        error.close()

        self.assertEqual(raised.exception.body, response_body)
        self.assertNotIn(sensitive_prompt, str(raised.exception))
        self.assertNotIn(sensitive_reasoning, str(raised.exception))
        serialized = json.dumps([call.args for call in warning.call_args_list], default=str)
        self.assertNotIn(sensitive_prompt, serialized)
        self.assertNotIn(sensitive_reasoning, serialized)

    def test_codex_text_failure_exception_excludes_sensitive_upstream_content(self) -> None:
        sensitive_reasoning = "SECRET_EVENT_REASONING"
        backend = mock.Mock()
        backend.iter_codex_text_response_events.return_value = iter([{
            "type": "response.failed",
            "response": {"error": {"message": sensitive_reasoning}},
        }])
        with (
            mock.patch.object(codex_text, "OpenAIBackendAPI", return_value=backend),
            mock.patch.object(
                codex_text.account_service,
                "get_text_access_token",
                side_effect=["token-a", RuntimeError("no more accounts")],
            ),
            mock.patch.object(codex_text.account_service, "get_account", return_value={}),
        ):
            with self.assertRaisesRegex(RuntimeError, "Codex text") as raised:
                list(codex_text.stream_codex_text_deltas(self._request()))

        self.assertNotIn(sensitive_reasoning, str(raised.exception))

    def test_codex_log_preview_redacts_data_urls_and_authorization(self) -> None:
        preview = OpenAIBackendAPI._codex_body_preview({
            "image_url": "data:image/png;base64,AAAA",
            "Authorization": "Bearer secret-token",
        })

        self.assertNotIn("AAAA", preview)
        self.assertNotIn("secret-token", preview)
        self.assertIn("[redacted]", preview)

    def test_codex_event_summary_redacts_delta_and_error_values(self) -> None:
        summary = OpenAIBackendAPI._codex_event_summary({
            "type": "response.output_text.delta",
            "delta": "data:image/png;base64,AAAA Bearer delta-secret",
            "error": {
                "type": "upstream_error",
                "message": "data:image/png;base64,BBBB Bearer error-secret",
            },
        })
        serialized = json.dumps(summary)

        self.assertNotIn("AAAA", serialized)
        self.assertNotIn("BBBB", serialized)
        self.assertNotIn("delta-secret", serialized)
        self.assertNotIn("error-secret", serialized)
        self.assertIn("[redacted]", serialized)

    def test_codex_http_error_log_filters_response_authorization_header(self) -> None:
        backend = OpenAIBackendAPI.__new__(OpenAIBackendAPI)
        backend.access_token = "request-secret"
        payload = {
            "model": "gpt-5.5",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "prompt"}]}],
        }

        with mock.patch("services.openai_backend_api.logger.warning") as warning:
            backend._log_codex_response_failure(
                "/backend-api/codex/responses",
                401,
                {"Authorization": "Bearer response-secret", "Retry-After": "3"},
                payload,
                {"error": "unauthorized"},
            )

        logged = warning.call_args.args[0]
        serialized = json.dumps(logged)
        self.assertNotIn("request-secret", serialized)
        self.assertNotIn("response-secret", serialized)
        self.assertNotIn("Authorization", logged["response"]["headers"])
        self.assertEqual(logged["response"]["headers"]["Retry-After"], "3")

    def test_streams_deltas_and_deduplicates_done_and_completed_text(self) -> None:
        deltas = self._collect_events([
            {"type": "response.output_text.delta", "delta": "Hel"},
            {"type": "response.output_text.delta", "delta": "lo"},
            {"type": "response.output_text.done", "text": "Hello"},
            {
                "type": "response.completed",
                "response": {
                    "status": "completed",
                    "output": [
                        {"type": "message", "content": [{"type": "output_text", "text": "Hello"}]}
                    ],
                },
            },
        ])

        self.assertEqual(deltas, ["Hel", "lo"])

    def test_uses_output_text_done_when_no_delta_was_emitted(self) -> None:
        self.assertEqual(
            self._collect_events([
                {"type": "response.output_text.done", "text": "fallback"},
                {"type": "response.completed", "response": {"status": "completed"}},
            ]),
            ["fallback"],
        )

    def test_uses_nested_completed_output_when_no_delta_or_done_exists(self) -> None:
        self.assertEqual(
            self._collect_events([
                {
                    "type": "response.completed",
                    "response": {
                        "status": "completed",
                        "output": [
                            {
                                "type": "message",
                                "content": [{"type": "output_text", "text": "nested final"}],
                            }
                        ],
                    },
                }
            ]),
            ["nested final"],
        )

    def test_failed_incomplete_and_error_events_raise(self) -> None:
        for event in (
            {"type": "response.failed", "response": {"error": {"message": "failed upstream"}}},
            {"type": "response.incomplete", "response": {"incomplete_details": {"reason": "max_output"}}},
            {"type": "error", "error": {"message": "bad event"}},
        ):
            with self.subTest(event_type=event["type"]):
                backend = mock.Mock()
                backend.iter_codex_text_response_events.return_value = iter([event])
                token_calls: list[set[str]] = []

                def token_for_attempt(_model, excluded_tokens, source_type):
                    self.assertEqual(source_type, "codex")
                    token_calls.append(set(excluded_tokens))
                    if not excluded_tokens:
                        return "token-a"
                    raise RuntimeError("no more accounts")

                with (
                    mock.patch.object(codex_text, "OpenAIBackendAPI", return_value=backend),
                    mock.patch.object(codex_text.account_service, "get_text_access_token", side_effect=token_for_attempt),
                    mock.patch.object(codex_text.account_service, "get_account", return_value={}),
                ):
                    with self.assertRaisesRegex(RuntimeError, "Codex text"):
                        list(codex_text.stream_codex_text_deltas(self._request()))

                self.assertEqual(token_calls, [set(), {"token-a"}])

    def test_completed_without_final_text_raises(self) -> None:
        backend = mock.Mock()
        backend.iter_codex_text_response_events.return_value = iter([
            {"type": "response.completed", "response": {"status": "completed", "output": []}}
        ])

        def token_for_attempt(_model, excluded_tokens, source_type):
            self.assertEqual(source_type, "codex")
            if not excluded_tokens:
                return "token-a"
            raise RuntimeError("no more accounts")

        with (
            mock.patch.object(codex_text, "OpenAIBackendAPI", return_value=backend),
            mock.patch.object(codex_text.account_service, "get_text_access_token", side_effect=token_for_attempt),
            mock.patch.object(codex_text.account_service, "get_account", return_value={}),
        ):
            with self.assertRaisesRegex(RuntimeError, "without final text"):
                list(codex_text.stream_codex_text_deltas(self._request()))

    def test_retries_another_codex_account_before_emitting_text(self) -> None:
        selected: list[set[str]] = []

        def token_for_attempt(_model, excluded_tokens, source_type):
            self.assertEqual(source_type, "codex")
            selected.append(set(excluded_tokens))
            return "token-a" if not excluded_tokens else "token-b"

        class FakeBackend:
            def __init__(self, access_token: str) -> None:
                self.access_token = access_token

            def iter_codex_text_response_events(self, **_kwargs):
                if self.access_token == "token-a":
                    raise RuntimeError("first account failed")
                return iter([
                    {"type": "response.output_text.done", "text": "second account"},
                    {"type": "response.completed", "response": {"status": "completed"}},
                ])

            def close(self) -> None:
                return None

        with (
            mock.patch.object(codex_text, "OpenAIBackendAPI", FakeBackend),
            mock.patch.object(codex_text.account_service, "get_text_access_token", side_effect=token_for_attempt),
            mock.patch.object(codex_text.account_service, "get_account", return_value={}),
            mock.patch.object(codex_text.account_service, "mark_text_used") as mark_used,
        ):
            result = list(codex_text.stream_codex_text_deltas(self._request()))

        self.assertEqual(result, ["second account"])
        self.assertEqual(selected, [set(), {"token-a"}])
        mark_used.assert_called_once_with("token-b")

    def test_does_not_retry_after_text_was_emitted(self) -> None:
        backend = mock.Mock()

        def events():
            yield {"type": "response.output_text.delta", "delta": "partial"}
            raise RuntimeError("stream broke")

        backend.iter_codex_text_response_events.return_value = events()
        with (
            mock.patch.object(codex_text, "OpenAIBackendAPI", return_value=backend),
            mock.patch.object(codex_text.account_service, "get_text_access_token", return_value="token-a") as select_token,
            mock.patch.object(codex_text.account_service, "get_account", return_value={}),
        ):
            stream = codex_text.stream_codex_text_deltas(self._request())
            self.assertEqual(next(stream), "partial")
            with self.assertRaisesRegex(RuntimeError, "stream broke"):
                next(stream)

        select_token.assert_called_once()

    def test_delta_then_eof_without_completed_raises_without_retry(self) -> None:
        backend = mock.Mock()
        backend.iter_codex_text_response_events.return_value = iter([
            {"type": "response.output_text.delta", "delta": "partial"}
        ])
        with (
            mock.patch.object(codex_text, "OpenAIBackendAPI", return_value=backend),
            mock.patch.object(codex_text.account_service, "get_text_access_token", return_value="token-a") as select_token,
            mock.patch.object(codex_text.account_service, "get_account", return_value={}),
            mock.patch.object(codex_text.account_service, "mark_text_used") as mark_used,
        ):
            stream = codex_text.stream_codex_text_deltas(self._request())
            self.assertEqual(next(stream), "partial")
            with self.assertRaisesRegex(RuntimeError, "successful terminal"):
                next(stream)

        select_token.assert_called_once()
        mark_used.assert_not_called()


if __name__ == "__main__":
    unittest.main()
