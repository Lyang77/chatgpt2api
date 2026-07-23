from __future__ import annotations

import unittest

from services.request_log_meta import build_image_request_meta, build_text_request_meta


class RequestLogMetaTests(unittest.TestCase):
    def test_image_meta_keeps_diagnostics_without_payload_content(self) -> None:
        payload = {
            "size": "1536x1024",
            "quality": "high",
            "n": 2,
            "output_format": "webp",
            "response_format": "url",
            "client_task_id": "task-1",
            "stream": True,
            "image": "data:image/png;base64,SECRET",
            "api_key": "SECRET",
        }

        self.assertEqual(
            build_image_request_meta(
                payload,
                mode="edit",
                reference_image_count=1,
                mask_image_count=1,
            ),
            {
                "mode": "edit",
                "size": "1536x1024",
                "quality": "high",
                "n": 2,
                "output_format": "webp",
                "response_format": "url",
                "client_task_id": "task-1",
                "stream": True,
                "reference_image_count": 1,
                "mask_image_count": 1,
            },
        )

    def test_image_meta_reports_effective_safe_defaults(self) -> None:
        self.assertEqual(
            build_image_request_meta({"client_task_id": "task-2"}, mode="generate"),
            {
                "mode": "generate",
                "quality": "auto",
                "n": 1,
                "output_format": "png",
                "response_format": "b64_json",
                "client_task_id": "task-2",
            },
        )

    def test_chat_meta_counts_structure_and_omits_secrets(self) -> None:
        payload = {
            "prompt": "fallback prompt",
            "messages": [
                {"role": "user", "content": [
                    {"type": "text", "text": "hello"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,SECRET"}},
                ]},
                {"role": "assistant", "content": "world"},
            ],
            "tools": [{
                "type": "function",
                "function": {"name": "secret_tool", "parameters": {"token": "SECRET"}},
            }],
            "tool_choice": {"type": "function", "name": "secret_tool"},
            "response_format": {"type": "json_schema", "json_schema": {"secret": "SECRET"}},
            "stream": True,
            "temperature": 0.4,
            "max_completion_tokens": 200,
            "authorization": "Bearer SECRET",
        }

        meta = build_text_request_meta(payload, protocol="chat_completions")

        self.assertEqual(meta["message_count"], 2)
        self.assertEqual(meta["role_counts"], {"user": 1, "assistant": 1})
        self.assertEqual(meta["tool_count"], 1)
        self.assertEqual(meta["image_input_count"], 1)
        self.assertEqual(meta["tool_choice_type"], "function")
        self.assertEqual(meta["response_format_type"], "json_schema")
        self.assertEqual(meta["prompt_chars"], len("fallback prompt"))
        self.assertIs(meta["stream"], True)
        self.assertEqual(meta["temperature"], 0.4)
        self.assertEqual(meta["max_completion_tokens"], 200)
        self.assertNotIn("tools", meta)
        self.assertNotIn("authorization", meta)
        self.assertNotIn("SECRET", repr(meta))
        self.assertNotIn("secret_tool", repr(meta))

    def test_responses_meta_records_input_and_reasoning_shape(self) -> None:
        payload = {
            "input": [
                {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hello"}]},
                {"type": "input_image", "image_url": "https://example.test/image.png"},
            ],
            "instructions": "be concise",
            "reasoning": {"effort": "high", "summary": "SECRET"},
            "tools": [{"type": "web_search_preview"}],
            "max_output_tokens": 500,
            "store": False,
        }

        meta = build_text_request_meta(payload, protocol="responses")

        self.assertEqual(meta["input_item_count"], 2)
        self.assertEqual(meta["image_input_count"], 1)
        self.assertEqual(meta["tool_count"], 1)
        self.assertEqual(meta["reasoning_effort"], "high")
        self.assertEqual(meta["input_chars"], len("hello"))
        self.assertEqual(meta["system_chars"], len("be concise"))
        self.assertEqual(meta["max_output_tokens"], 500)
        self.assertIs(meta["store"], False)
        self.assertNotIn("SECRET", repr(meta))

    def test_messages_and_editable_meta_use_safe_counts(self) -> None:
        messages = build_text_request_meta({
            "system": "system text",
            "messages": [{"role": "user", "content": "hello"}],
            "tools": [{"name": "lookup", "input_schema": {"secret": "SECRET"}}],
            "max_tokens": 1000,
            "top_p": 0.8,
        }, protocol="messages")
        editable = build_text_request_meta({
            "prompt": "make slides",
            "client_task_id": "deck-1",
            "base64_images": ["SECRET-A", "SECRET-B"],
        }, protocol="editable_file")

        self.assertEqual(messages["message_count"], 1)
        self.assertEqual(messages["tool_count"], 1)
        self.assertEqual(messages["system_chars"], len("system text"))
        self.assertEqual(messages["max_tokens"], 1000)
        self.assertEqual(editable, {
            "client_task_id": "deck-1",
            "reference_image_count": 2,
            "prompt_chars": len("make slides"),
        })
        self.assertNotIn("SECRET", repr(messages))
        self.assertNotIn("SECRET", repr(editable))


if __name__ == "__main__":
    unittest.main()
