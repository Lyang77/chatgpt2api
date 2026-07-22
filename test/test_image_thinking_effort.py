import unittest
from unittest import mock

from services.config import config
from services.openai_backend_api import ChatRequirements, OpenAIBackendAPI


class ImageThinkingEffortPayloadTests(unittest.TestCase):
    def make_backend(self) -> OpenAIBackendAPI:
        backend = OpenAIBackendAPI.__new__(OpenAIBackendAPI)
        backend.base_url = "https://chatgpt.com"
        backend.session = mock.Mock()
        backend._image_headers = mock.Mock(return_value={})
        response = mock.Mock()
        response.status_code = 200
        response.json.return_value = {"conduit_token": "conduit-token"}
        backend.session.post.return_value = response
        return backend

    def image_requests(self, configured_effort: str) -> tuple[str, dict, str, dict]:
        backend = self.make_backend()
        requirements = ChatRequirements(token="requirements-token")

        with mock.patch.dict(config.data, {"image_thinking_effort": configured_effort}):
            conduit_token, parent_message_id = backend._prepare_image_conversation(
                "draw",
                requirements,
                "gpt-image-2",
            )
            prepare_url = backend.session.post.call_args.args[0]
            prepare_payload = backend.session.post.call_args.kwargs["json"]

            backend.session.post.reset_mock()
            backend._start_image_generation(
                "draw",
                requirements,
                conduit_token,
                "gpt-image-2",
                parent_message_id=parent_message_id,
            )
            conversation_url = backend.session.post.call_args.args[0]
            conversation_payload = backend.session.post.call_args.kwargs["json"]

        return prepare_url, prepare_payload, conversation_url, conversation_payload

    def test_configured_effort_is_omitted_from_picture_v2_requests(self) -> None:
        prepare_url, prepare_payload, conversation_url, conversation_payload = self.image_requests("high")

        self.assertEqual(prepare_url, "https://chatgpt.com/backend-api/f/conversation/prepare")
        self.assertEqual(conversation_url, "https://chatgpt.com/backend-api/f/conversation")
        self.assertEqual(prepare_payload["model"], "gpt-5-3")
        self.assertEqual(conversation_payload["model"], "gpt-5-3")
        self.assertEqual(prepare_payload["system_hints"], ["picture_v2"])
        self.assertEqual(conversation_payload["system_hints"], ["picture_v2"])
        self.assertEqual(conversation_payload["parent_message_id"], prepare_payload["parent_message_id"])
        self.assertNotIn("thinking_effort", prepare_payload)
        self.assertNotIn("thinking_effort", conversation_payload)

    def test_disabled_effort_is_omitted_from_prepare_and_conversation(self) -> None:
        _, prepare_payload, _, conversation_payload = self.image_requests("")

        self.assertNotIn("thinking_effort", prepare_payload)
        self.assertNotIn("thinking_effort", conversation_payload)


if __name__ == "__main__":
    unittest.main()
