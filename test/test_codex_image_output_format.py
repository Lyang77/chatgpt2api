from __future__ import annotations

import json
import unittest
from unittest import mock

from services.openai_backend_api import OpenAIBackendAPI
from services.protocol import openai_v1_image_edit, openai_v1_image_generations
from services.protocol.conversation import ImageOutput, stream_codex_image_outputs


class CodexImageOutputFormatTests(unittest.TestCase):
    def test_image_protocols_copy_output_format_to_conversation_request(self):
        captured = []

        def fake_stream(request):
            captured.append(request)
            return [
                ImageOutput(
                    kind="result",
                    model=request.model,
                    index=1,
                    total=1,
                    data=[{"b64_json": "ZmFrZQ=="}],
                )
            ]

        with mock.patch.object(
            openai_v1_image_generations,
            "stream_image_outputs_with_pool",
            fake_stream,
        ):
            openai_v1_image_generations.handle(
                {
                    "model": "codex-gpt-image-2",
                    "prompt": "generate",
                    "output_format": "jpeg",
                }
            )

        with mock.patch.object(
            openai_v1_image_edit,
            "stream_image_outputs_with_pool",
            fake_stream,
        ):
            openai_v1_image_edit.handle(
                {
                    "model": "codex-gpt-image-2",
                    "prompt": "edit",
                    "images": [(b"image", "image.png", "image/png")],
                    "output_format": "webp",
                }
            )

        self.assertEqual([request.output_format for request in captured], ["jpeg", "webp"])

    def test_codex_stream_forwards_output_format_to_backend(self):
        backend = mock.Mock()
        backend.iter_codex_image_response_events.return_value = [
            {"type": "image_generation_call", "result": "ZmFrZQ=="}
        ]
        request = mock.Mock(
            prompt="edit",
            images=["data:image/png;base64,ZmFrZQ=="],
            size="1024x1024",
            quality="auto",
            output_format="jpeg",
            response_format="b64_json",
            base_url=None,
            model="codex-gpt-image-2",
        )

        outputs = list(stream_codex_image_outputs(backend, request))

        self.assertEqual(len(outputs), 1)
        backend.iter_codex_image_response_events.assert_called_once_with(
            prompt="edit",
            images=["data:image/png;base64,ZmFrZQ=="],
            size="1024x1024",
            quality="auto",
            output_format="jpeg",
        )

    def test_codex_backend_uses_requested_output_format_in_tool_payload(self):
        backend = OpenAIBackendAPI.__new__(OpenAIBackendAPI)
        backend.access_token = "token"
        backend.base_url = "https://chatgpt.com"
        backend._ensure_codex_source_account = mock.Mock()
        backend._codex_image_input = mock.Mock(return_value=[])
        backend._codex_responses_headers = mock.Mock(return_value={})
        backend._iter_codex_response_events = mock.Mock(return_value=iter(()))

        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.__exit__.return_value = False

        with (
            mock.patch("services.openai_backend_api.urllib.request.urlopen", return_value=response) as urlopen,
            mock.patch("services.openai_backend_api.account_service.get_account", return_value={}),
            mock.patch("services.openai_backend_api.account_service._decode_jwt_payload", return_value={}),
        ):
            list(
                backend.iter_codex_image_response_events(
                    "generate",
                    size="1024x1024",
                    output_format="jpeg",
                )
            )

        request = urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["tools"][0]["output_format"], "jpeg")


if __name__ == "__main__":
    unittest.main()
