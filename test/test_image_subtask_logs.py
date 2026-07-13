from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from services.log_service import ImageTaskLogContext, ImageTaskRegistry, LogService, create_image_task_log_context
from services.protocol import conversation, openai_v1_image_generations


class ImageTaskRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.service = LogService(Path(self.temp_dir.name) / "logs.jsonl")
        self.registry = ImageTaskRegistry()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_request_stop_marks_running_log_and_signals_registered_worker(self) -> None:
        item = self.service.create_call(
            {"status": "running", "endpoint": "/v1/images/generations"},
            "文生图 进行中",
        )
        event = self.registry.register(item["id"])

        stopped, updated = self.service.request_stop(item["id"], self.registry)

        self.assertTrue(stopped)
        self.assertTrue(event.is_set())
        self.assertEqual(updated["detail"]["status"], "running")
        self.assertIn("stop_requested_at", updated["detail"])

    def test_request_stop_finishes_orphan_running_log(self) -> None:
        item = self.service.create_call(
            {"status": "running", "endpoint": "/v1/images/generations"},
            "文生图 进行中",
        )

        stopped, updated = self.service.request_stop(item["id"], self.registry)

        self.assertTrue(stopped)
        self.assertEqual(updated["detail"]["status"], "stopped")

    def test_image_task_context_creates_one_running_log_with_batch_metadata(self) -> None:
        context = create_image_task_log_context(
            self.service,
            self.registry,
            {"endpoint": "/v1/images/edits", "model": "gpt-image-2", "request_text": "draw product"},
            batch_id="batch-1",
            image_index=2,
            image_total=4,
        )

        item = self.service.get_by_id(context.log_id)

        self.assertEqual(item["summary"], "文生图")
        self.assertEqual(item["detail"]["status"], "running")
        self.assertEqual(item["detail"]["batch_id"], "batch-1")
        self.assertEqual(item["detail"]["image_index"], 2)
        self.assertEqual(item["detail"]["image_total"], 4)
        self.assertEqual(item["detail"]["stage"], "getting_account")

    def test_image_generation_handler_passes_log_template_to_conversation_request(self) -> None:
        with mock.patch.object(openai_v1_image_generations, "stream_image_outputs_with_pool", return_value=iter(())) as stream:
            openai_v1_image_generations.handle({
                "prompt": "draw product",
                "model": "gpt-image-2",
                "n": 1,
                "image_task_log_template": {"endpoint": "/v1/images/generations", "request_text": "draw product"},
                "image_task_batch_id": "batch-1",
            })

        request = stream.call_args.args[0]
        self.assertEqual(request.image_task_batch_id, "batch-1")
        self.assertEqual(request.image_task_log_template["request_text"], "draw product")

    def test_stop_after_account_selection_releases_the_held_slot_once(self) -> None:
        event = self.registry.register("task-1")
        context = ImageTaskLogContext("task-1", "batch-1", 1, 1, event)
        request = conversation.ConversationRequest(model="gpt-image-2", prompt="draw")

        def get_account(_token: str) -> dict[str, str]:
            event.set()
            return {"email": "a@example.test"}

        with mock.patch.object(conversation.account_service, "get_available_access_token", return_value="token-1"), \
             mock.patch.object(conversation.account_service, "get_account", side_effect=get_account), \
             mock.patch.object(conversation.account_service, "mark_image_result") as mark:
            with self.assertRaises(conversation.ImageGenerationStopped):
                conversation._generate_single_image_with_context(request, 1, 1, context)

        mark.assert_called_once_with("token-1", False)

    def test_completed_subtask_log_records_actual_image_count(self) -> None:
        request = conversation.ConversationRequest(
            model="gpt-image-2",
            prompt="draw variants",
            image_task_log_template={"endpoint": "/v1/images/generations"},
            image_task_batch_id="batch-1",
        )
        context = mock.Mock(log_id="log-1")
        outputs = [conversation.ImageOutput(
            kind="result",
            model="gpt-image-2",
            index=1,
            total=1,
            data=[{"url": "/one.png"}, {"url": "/two.png"}],
            completion_reason="upstream_completed",
        )]

        with (
            mock.patch.object(conversation, "create_image_task_log_context", return_value=context),
            mock.patch.object(conversation, "_generate_single_image_with_context", return_value=outputs),
            mock.patch.object(conversation, "_update_image_task_log") as update_log,
            mock.patch.object(conversation.image_task_registry, "unregister"),
        ):
            result = conversation._generate_single_image(request, 1, 1)

        self.assertEqual(result, outputs)
        final_call = update_log.call_args_list[-1]
        self.assertEqual(final_call.kwargs["actual_image_count"], 2)
        self.assertEqual(final_call.kwargs["completion_reason"], "upstream_completed")


if __name__ == "__main__":
    unittest.main()
