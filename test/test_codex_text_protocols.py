from __future__ import annotations

import unittest
from unittest import mock

from fastapi import HTTPException

from services.account_service import AccountModelUnavailableError
from services.config import config
from services.protocol import openai_v1_chat_complete, openai_v1_response
from services.protocol.chat_completion_cache import chat_completion_cache
from services.protocol.conversation import ImageOutput
from utils.helper import is_codex_text_model


class CodexChatCompletionTests(unittest.TestCase):
    GPT_5_6_MODELS = ("gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.6-sol")

    def setUp(self) -> None:
        self.old_cache_settings = config.data.get("chat_completion_cache")
        config.data["chat_completion_cache"] = {"enabled": False}
        chat_completion_cache.clear()

    def tearDown(self) -> None:
        if self.old_cache_settings is None:
            config.data.pop("chat_completion_cache", None)
        else:
            config.data["chat_completion_cache"] = self.old_cache_settings
        chat_completion_cache.clear()

    @staticmethod
    def _body(*, stream: bool = False) -> dict:
        return {
            "model": "gpt-5.5",
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": "build prompt"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "https://example.test/product.png"},
                    },
                ],
            }],
            "stream": stream,
        }

    def _fake_codex_deltas(self, request):
        self.assertEqual(request.model, "gpt-5.5")
        self.assertEqual(request.reasoning_effort, "low")
        self.assertEqual(request.instructions, "")
        self.assertEqual(request.input_items, [{
            "role": "user",
            "content": [
                {"type": "input_text", "text": "build prompt"},
                {"type": "input_image", "image_url": "https://example.test/product.png"},
            ],
        }])
        request.account_email = "codex@example.test"
        yield "new "
        yield "prompt"

    def test_non_stream_codex_completion_collects_text_and_preserves_account_email(self) -> None:
        with (
            mock.patch.object(
                openai_v1_chat_complete,
                "stream_codex_text_deltas",
                side_effect=self._fake_codex_deltas,
                create=True,
            ) as stream_codex,
            mock.patch.object(openai_v1_chat_complete, "normalize_messages", side_effect=lambda value: value),
            mock.patch.object(openai_v1_chat_complete, "text_backend", return_value=object()),
            mock.patch.object(openai_v1_chat_complete, "collect_text", return_value="web fallback"),
        ):
            response = openai_v1_chat_complete.handle(self._body())

        self.assertEqual(response["object"], "chat.completion")
        self.assertEqual(response["model"], "gpt-5.5")
        self.assertEqual(response["choices"][0]["message"]["content"], "new prompt")
        self.assertEqual(response["_account_email"], "codex@example.test")
        stream_codex.assert_called_once()

    def test_gpt_5_6_models_use_codex_chat_adapter_and_preserve_model(self) -> None:
        for model in self.GPT_5_6_MODELS:
            with self.subTest(model=model):
                def fake_codex_deltas(request):
                    self.assertEqual(request.model, model)
                    self.assertEqual(request.reasoning_effort, "low")
                    yield "codex answer"

                with (
                    mock.patch.object(
                        openai_v1_chat_complete,
                        "stream_codex_text_deltas",
                        side_effect=fake_codex_deltas,
                    ) as stream_codex,
                    mock.patch.object(
                        openai_v1_chat_complete,
                        "text_backend",
                        side_effect=AssertionError(f"{model} must not use the Web backend"),
                    ) as web_backend,
                ):
                    response = openai_v1_chat_complete.handle({
                        "model": model,
                        "messages": [{"role": "user", "content": "hello"}],
                    })

                stream_codex.assert_called_once()
                web_backend.assert_not_called()
                self.assertEqual(response["model"], model)
                self.assertEqual(response["choices"][0]["message"]["content"], "codex answer")

    def test_codex_chat_reasoning_effort_uses_valid_request_override(self) -> None:
        cases = (
            ({"reasoning_effort": "high"}, "high"),
            ({"thinking_effort": "medium"}, "medium"),
            ({"reasoning": {"effort": "extended"}}, "extended"),
            ({"reasoning_effort": "invalid"}, "low"),
        )
        for extra, expected in cases:
            with self.subTest(extra=extra):
                _messages, request = openai_v1_chat_complete.codex_chat_request({**self._body(), **extra})
                self.assertEqual(request.reasoning_effort, expected)

    def test_gpt_5_6_sol_chat_tool_rejection_mentions_requested_model(self) -> None:
        model = "gpt-5.6-sol"
        with (
            mock.patch.object(
                openai_v1_chat_complete,
                "stream_codex_text_deltas",
                side_effect=AssertionError("tool rejection must happen before Codex transport"),
            ),
            mock.patch.object(openai_v1_chat_complete, "text_backend", return_value=object()),
            mock.patch.object(openai_v1_chat_complete, "collect_text", return_value="unexpected Web response"),
        ):
            with self.assertRaises(HTTPException) as raised:
                openai_v1_chat_complete.handle({
                    "model": model,
                    "messages": [{"role": "user", "content": "use a tool"}],
                    "tools": [{"type": "function", "name": "run"}],
                })

        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn(model, str(raised.exception.detail))
        self.assertNotIn("gpt-5.5", str(raised.exception.detail))

    def test_stream_codex_completion_emits_role_deltas_and_stop(self) -> None:
        with (
            mock.patch.object(
                openai_v1_chat_complete,
                "stream_codex_text_deltas",
                side_effect=self._fake_codex_deltas,
                create=True,
            ) as stream_codex,
            mock.patch.object(openai_v1_chat_complete, "normalize_messages", side_effect=lambda value: value),
            mock.patch.object(openai_v1_chat_complete, "text_backend", return_value=object()),
            mock.patch.object(openai_v1_chat_complete, "stream_text_deltas", return_value=iter(["web fallback"])),
        ):
            chunks = list(openai_v1_chat_complete.handle(self._body(stream=True)))

        self.assertEqual(len(chunks), 4)
        self.assertEqual(chunks[0]["choices"][0]["delta"], {"role": "assistant"})
        self.assertEqual(chunks[1]["choices"][0]["delta"], {"content": "new "})
        self.assertEqual(chunks[2]["choices"][0]["delta"], {"content": "prompt"})
        self.assertEqual(chunks[3]["choices"][0]["delta"], {})
        self.assertEqual(chunks[3]["choices"][0]["finish_reason"], "stop")
        self.assertTrue(all(chunk["model"] == "gpt-5.5" for chunk in chunks))
        self.assertTrue(all(chunk["_account_email"] == "codex@example.test" for chunk in chunks))
        stream_codex.assert_called_once()

    def test_codex_completion_rejects_nonempty_tools_and_tool_choice(self) -> None:
        unsupported = (
            {"tools": [{"type": "function", "function": {"name": "run"}}]},
            {"tool_choice": "auto"},
        )
        for extra in unsupported:
            with self.subTest(extra=extra), mock.patch.object(
                openai_v1_chat_complete, "normalize_messages", side_effect=lambda value: value
            ), mock.patch.object(openai_v1_chat_complete, "text_backend", return_value=object()), mock.patch.object(
                openai_v1_chat_complete, "collect_text", return_value="unexpected"
            ):
                with self.assertRaises(HTTPException) as raised:
                    openai_v1_chat_complete.handle({**self._body(), **extra})

                self.assertEqual(raised.exception.status_code, 400)
                self.assertIn("does not support tools", str(raised.exception.detail))

    def test_codex_completion_rejects_messages_without_user_or_assistant_content(self) -> None:
        invalid_messages = (
            [{"role": "system", "content": "instructions only"}],
            [{"role": "developer", "content": "instructions only"}],
            [{"role": "user", "content": ""}],
            [{"role": "user", "content": "   "}],
            [{"role": "user", "content": [{"type": "audio", "data": "ignored"}]}],
        )
        for messages in invalid_messages:
            with self.subTest(messages=messages), mock.patch.object(
                openai_v1_chat_complete,
                "stream_codex_text_deltas",
                side_effect=AssertionError("invalid input must fail before transport"),
            ):
                with self.assertRaises(HTTPException) as raised:
                    openai_v1_chat_complete.handle({"model": "gpt-5.5", "messages": messages})

                self.assertEqual(raised.exception.status_code, 400)
                self.assertIn("messages are required", str(raised.exception.detail))

    def test_stream_codex_completion_rejects_empty_messages_before_transport(self) -> None:
        with mock.patch.object(
            openai_v1_chat_complete,
            "stream_codex_text_deltas",
            side_effect=AssertionError("invalid input must fail before transport"),
        ):
            stream = openai_v1_chat_complete.handle({
                "model": "gpt-5.5",
                "stream": True,
                "messages": [{"role": "user", "content": ""}],
            })
            with self.assertRaises(HTTPException) as raised:
                next(stream)

        self.assertEqual(raised.exception.status_code, 400)

    def test_hyphenated_gpt_5_5_keeps_using_web_text_backend(self) -> None:
        backend = object()
        body = {
            "model": "gpt-5-5",
            "messages": [{"role": "user", "content": "stay on web"}],
        }
        with (
            mock.patch.object(openai_v1_chat_complete, "text_backend", return_value=backend) as web_backend,
            mock.patch.object(openai_v1_chat_complete, "collect_text", return_value="web answer") as collect,
            mock.patch.object(
                openai_v1_chat_complete,
                "stream_codex_text_deltas",
                side_effect=AssertionError("gpt-5-5 must not use the Codex backend"),
                create=True,
            ),
        ):
            response = openai_v1_chat_complete.handle(body)

        web_backend.assert_called_once_with("gpt-5-5")
        collect.assert_called_once()
        self.assertIs(collect.call_args.args[0], backend)
        self.assertEqual(response["choices"][0]["message"]["content"], "web answer")

    def test_codex_text_model_match_rejects_case_and_whitespace_variants(self) -> None:
        for model in ("GPT-5.5", " gpt-5.5 "):
            with self.subTest(model=model):
                self.assertFalse(is_codex_text_model(model))

    def test_non_exact_gpt_5_5_names_keep_using_web_text_backend(self) -> None:
        for model in ("GPT-5.5", " gpt-5.5 "):
            with self.subTest(model=model):
                backend = object()
                body = {
                    "model": model,
                    "messages": [{"role": "user", "content": "exact routing only"}],
                }
                with (
                    mock.patch.object(openai_v1_chat_complete, "text_backend", return_value=backend) as web_backend,
                    mock.patch.object(openai_v1_chat_complete, "collect_text", return_value="web answer") as collect,
                    mock.patch.object(
                        openai_v1_chat_complete,
                        "stream_codex_text_deltas",
                        return_value=iter(["codex answer"]),
                    ),
                ):
                    response = openai_v1_chat_complete.handle(body)

                web_backend.assert_called_once()
                collect.assert_called_once()
                self.assertIs(collect.call_args.args[0], backend)
                self.assertEqual(response["choices"][0]["message"]["content"], "web answer")


class CodexResponsesTests(unittest.TestCase):
    GPT_5_6_MODELS = CodexChatCompletionTests.GPT_5_6_MODELS

    def setUp(self) -> None:
        self.old_cache_settings = config.data.get("chat_completion_cache")
        config.data["chat_completion_cache"] = {"enabled": False}
        chat_completion_cache.clear()

    def tearDown(self) -> None:
        if self.old_cache_settings is None:
            config.data.pop("chat_completion_cache", None)
        else:
            config.data["chat_completion_cache"] = self.old_cache_settings
        chat_completion_cache.clear()

    def test_non_stream_codex_response_accepts_string_input_and_preserves_account_email(self) -> None:
        def fake_codex_deltas(request):
            self.assertEqual(request.model, "gpt-5.5")
            self.assertEqual(request.reasoning_effort, "low")
            self.assertEqual(request.instructions, "generate a new prompt")
            self.assertEqual(request.input_items, [{
                "role": "user",
                "content": [{"type": "input_text", "text": "template"}],
            }])
            request.account_email = "codex@example.test"
            yield "new "
            yield "prompt"

        body = {
            "model": "gpt-5.5",
            "instructions": "generate a new prompt",
            "input": "template",
        }
        with (
            mock.patch.object(
                openai_v1_response,
                "stream_codex_text_deltas",
                side_effect=fake_codex_deltas,
                create=True,
            ) as stream_codex,
            mock.patch.object(openai_v1_response, "normalize_messages", side_effect=lambda value: value),
            mock.patch.object(openai_v1_response, "text_backend", return_value=object()),
            mock.patch.object(openai_v1_response, "stream_text_deltas", return_value=iter(["web fallback"])),
        ):
            response = openai_v1_response.handle(body)

        self.assertEqual(response["status"], "completed")
        self.assertEqual(response["model"], "gpt-5.5")
        self.assertEqual(response["output"][0]["content"][0]["text"], "new prompt")
        self.assertEqual(response["_account_email"], "codex@example.test")
        stream_codex.assert_called_once()

    def test_gpt_5_6_models_use_codex_responses_adapter_and_preserve_model(self) -> None:
        for model in self.GPT_5_6_MODELS:
            with self.subTest(model=model):
                def fake_codex_deltas(request):
                    self.assertEqual(request.model, model)
                    self.assertEqual(request.reasoning_effort, "low")
                    yield "codex answer"

                with (
                    mock.patch.object(
                        openai_v1_response,
                        "stream_codex_text_deltas",
                        side_effect=fake_codex_deltas,
                    ) as stream_codex,
                    mock.patch.object(
                        openai_v1_response,
                        "text_backend",
                        side_effect=AssertionError(f"{model} must not use the Web backend"),
                    ) as web_backend,
                ):
                    response = openai_v1_response.handle({"model": model, "input": "hello"})

                stream_codex.assert_called_once()
                web_backend.assert_not_called()
                self.assertEqual(response["model"], model)
                self.assertEqual(response["output"][0]["content"][0]["text"], "codex answer")

    def test_codex_responses_reasoning_effort_uses_valid_request_override(self) -> None:
        cases = (
            ({"reasoning": {"effort": "high"}}, "high"),
            ({"thinking_effort": "medium"}, "medium"),
            ({"reasoning_effort": "extended"}, "extended"),
            ({"reasoning": {"effort": "invalid"}}, "low"),
        )
        for extra, expected in cases:
            with self.subTest(extra=extra):
                _messages, request = openai_v1_response.codex_response_request({
                    "model": "gpt-5.5",
                    "input": "hello",
                    **extra,
                })
                self.assertEqual(request.reasoning_effort, expected)

    def test_gpt_5_6_sol_responses_tool_rejection_mentions_requested_model(self) -> None:
        model = "gpt-5.6-sol"
        with (
            mock.patch.object(
                openai_v1_response,
                "stream_codex_text_deltas",
                side_effect=AssertionError("tool rejection must happen before Codex transport"),
            ),
            mock.patch.object(openai_v1_response, "text_backend", return_value=object()),
            mock.patch.object(openai_v1_response, "stream_text_deltas", return_value=iter(["unexpected Web response"])),
        ):
            with self.assertRaises(HTTPException) as raised:
                openai_v1_response.handle({
                    "model": model,
                    "input": "use a tool",
                    "tools": [{"type": "function", "name": "run"}],
                })

        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn(model, str(raised.exception.detail))
        self.assertNotIn("gpt-5.5", str(raised.exception.detail))

    def test_stream_codex_response_preserves_message_order_multi_images_and_event_order(self) -> None:
        def fake_codex_deltas(request):
            self.assertEqual(request.model, "gpt-5.5")
            self.assertEqual(
                request.instructions,
                "generate a new prompt\n\nkeep brand facts\n\nreturn only the prompt",
            )
            self.assertEqual(request.input_items, [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "template"},
                        {"type": "input_image", "image_url": "data:image/png;base64,AAAA"},
                        {"type": "input_image", "image_url": "https://example.test/detail.png"},
                    ],
                },
                {
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "previous prompt"}],
                },
            ])
            request.account_email = "codex@example.test"
            yield "new "
            yield "prompt"

        body = {
            "model": "gpt-5.5",
            "stream": True,
            "instructions": "generate a new prompt",
            "input": [
                {"role": "system", "content": "keep brand facts"},
                {"role": "developer", "content": [{"type": "input_text", "text": "return only the prompt"}]},
                {"role": "user", "content": [
                    {"type": "input_text", "text": "template"},
                    {"type": "input_image", "image_url": "data:image/png;base64,AAAA"},
                    {"type": "input_image", "image_url": "https://example.test/detail.png"},
                ]},
                {"role": "assistant", "content": [{"type": "output_text", "text": "previous prompt"}]},
            ],
        }
        with (
            mock.patch.object(
                openai_v1_response,
                "stream_codex_text_deltas",
                side_effect=fake_codex_deltas,
                create=True,
            ) as stream_codex,
            mock.patch.object(openai_v1_response, "normalize_messages", side_effect=lambda value: value),
            mock.patch.object(openai_v1_response, "text_backend", return_value=object()),
            mock.patch.object(openai_v1_response, "stream_text_deltas", return_value=iter(["web fallback"])),
        ):
            events = list(openai_v1_response.handle(body))

        self.assertEqual([event["type"] for event in events], [
            "response.created",
            "response.output_item.added",
            "response.content_part.added",
            "response.output_text.delta",
            "response.output_text.delta",
            "response.output_text.done",
            "response.content_part.done",
            "response.output_item.done",
            "response.completed",
        ])
        self.assertEqual(
            [event["delta"] for event in events if event["type"] == "response.output_text.delta"],
            ["new ", "prompt"],
        )
        self.assertEqual(events[-4]["text"], "new prompt")
        self.assertEqual(events[-3]["part"]["text"], "new prompt")
        self.assertEqual(events[-1]["response"]["output"][0]["content"][0]["text"], "new prompt")
        self.assertTrue(all(
            event.get("_account_email") == "codex@example.test"
            for event in events
            if event["type"] not in {"response.created", "response.output_item.added"}
        ))
        stream_codex.assert_called_once()

    def test_stream_codex_response_does_not_emit_success_events_before_first_delta(self) -> None:
        failures = (
            AccountModelUnavailableError("gpt-5.5"),
            RuntimeError("upstream failed before first text event"),
        )
        for failure in failures:
            with self.subTest(failure=type(failure).__name__), mock.patch.object(
                openai_v1_response,
                "stream_codex_text_deltas",
                side_effect=failure,
            ):
                events = openai_v1_response.handle({
                    "model": "gpt-5.5",
                    "stream": True,
                    "input": "template",
                })
                with self.assertRaises(type(failure)):
                    next(events)

    def test_codex_response_rejects_nonempty_tools_and_tool_choice(self) -> None:
        for extra in (
            {"tools": [{"type": "web_search"}]},
            {"tools": [{"type": "image_generation"}]},
            {"tools": [{"type": "function", "name": "run"}]},
            {"tool_choice": "auto"},
        ):
            with self.subTest(extra=extra), mock.patch.object(
                openai_v1_response,
                "stream_codex_text_deltas",
                side_effect=AssertionError("tool requests must fail before transport"),
                create=True,
            ), mock.patch.object(
                openai_v1_response,
                "stream_image_outputs_with_pool",
                side_effect=AssertionError("Codex text must not enter image generation"),
            ), mock.patch.object(
                openai_v1_response,
                "run_web_search",
                side_effect=AssertionError("Codex text must not enter Web Search"),
            ):
                with self.assertRaises(HTTPException) as raised:
                    openai_v1_response.handle({
                        "model": "gpt-5.5",
                        "input": "template",
                        **extra,
                    })

                self.assertEqual(raised.exception.status_code, 400)
                self.assertIn("does not support tools", str(raised.exception.detail))

    def test_codex_response_rejects_empty_input(self) -> None:
        for input_value in ("", [], [{"role": "system", "content": "instructions only"}]):
            with self.subTest(input=input_value), mock.patch.object(
                openai_v1_response,
                "stream_codex_text_deltas",
                side_effect=AssertionError("empty input must fail before transport"),
                create=True,
            ):
                with self.assertRaises(HTTPException) as raised:
                    openai_v1_response.handle({"model": "gpt-5.5", "input": input_value})

                self.assertEqual(raised.exception.status_code, 400)
                self.assertIn("input is required", str(raised.exception.detail))

    def test_non_codex_web_search_and_image_generation_tools_keep_existing_routes(self) -> None:
        search_result = {
            "answer": "Web answer.",
            "sources": [{"title": "Example", "url": "https://example.test", "snippet": ""}],
        }
        with mock.patch.object(openai_v1_response, "run_web_search", return_value=search_result) as search:
            web_response = openai_v1_response.handle({
                "model": "gpt-5-5",
                "input": "latest facts",
                "tools": [{"type": "web_search"}],
            })

        search.assert_called_once_with("latest facts", "gpt-5-5")
        self.assertEqual(web_response["output"][0]["type"], "web_search_call")

        image = ImageOutput(
            kind="result",
            model="gpt-image-2",
            index=1,
            total=1,
            data=[{"b64_json": "ZmFrZQ=="}],
        )
        with mock.patch.object(
            openai_v1_response,
            "stream_image_outputs_with_pool",
            return_value=iter([image]),
        ) as stream_image:
            image_response = openai_v1_response.handle({
                "model": "gpt-image-2",
                "input": "draw a product",
                "tools": [{"type": "image_generation"}],
            })

        stream_image.assert_called_once()
        self.assertEqual(image_response["output"][0]["type"], "image_generation_call")

    def test_non_exact_codex_model_name_keeps_using_web_text_backend(self) -> None:
        for model in ("GPT-5.5", " gpt-5.5 "):
            with self.subTest(model=model):
                backend = object()
                with (
                    mock.patch.object(openai_v1_response, "normalize_messages", side_effect=lambda value: value),
                    mock.patch.object(openai_v1_response, "text_backend", return_value=backend) as web_backend,
                    mock.patch.object(openai_v1_response, "stream_text_deltas", return_value=iter(["web answer"])),
                    mock.patch.object(
                        openai_v1_response,
                        "stream_codex_text_deltas",
                        side_effect=AssertionError("non-exact model must stay on Web"),
                        create=True,
                    ),
                ):
                    response = openai_v1_response.handle({"model": model, "input": "hello"})

                web_backend.assert_called_once_with("gpt-5.5" if model.startswith(" ") else model)
                self.assertEqual(response["output"][0]["content"][0]["text"], "web answer")


if __name__ == "__main__":
    unittest.main()
