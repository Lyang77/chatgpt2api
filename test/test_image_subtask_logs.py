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

    def test_request_stop_finishes_running_log_and_signals_registered_worker(self) -> None:
        item = self.service.create_call(
            {"status": "running", "endpoint": "/v1/images/generations"},
            "文生图 进行中",
        )
        event = self.registry.register(item["id"])

        stopped, updated = self.service.request_stop(item["id"], self.registry)

        self.assertTrue(stopped)
        self.assertTrue(event.is_set())
        self.assertEqual(updated["detail"]["status"], "stopped")
        self.assertEqual(updated["detail"]["stage"], "stopped")
        self.assertIn("stop_requested_at", updated["detail"])
        self.assertIn("stopped_at", updated["detail"])

    def test_stopped_log_cannot_be_overwritten_by_late_worker_update(self) -> None:
        item = self.service.create_call(
            {"status": "running", "endpoint": "/v1/images/generations"},
            "文生图 进行中",
        )
        self.registry.register(item["id"])
        self.service.request_stop(item["id"], self.registry)

        updated = self.service.update_call(
            item["id"],
            detail_patch={"status": "success", "stage": "success", "actual_image_count": 1},
        )

        self.assertEqual(updated["detail"]["status"], "stopped")
        self.assertEqual(updated["detail"]["stage"], "stopped")
        self.assertNotIn("actual_image_count", updated["detail"])

    def test_request_stop_finishes_orphan_running_log(self) -> None:
        item = self.service.create_call(
            {"status": "running", "endpoint": "/v1/images/generations"},
            "文生图 进行中",
        )

        stopped, updated = self.service.request_stop(item["id"], self.registry)

        self.assertTrue(stopped)
        self.assertEqual(updated["detail"]["status"], "stopped")

    def test_request_stop_finishes_queued_log(self) -> None:
        item = self.service.create_call(
            {"status": "queued", "stage": "getting_account", "endpoint": "/v1/images/generations"},
            "文生图",
        )
        event = self.registry.register(item["id"])

        stopped, updated = self.service.request_stop(item["id"], self.registry)

        self.assertTrue(stopped)
        self.assertTrue(event.is_set())
        self.assertEqual(updated["detail"]["status"], "stopped")

    def test_startup_recovery_stops_orphaned_image_logs_only(self) -> None:
        running_image = self.service.create_call(
            {"status": "running", "endpoint": "/v1/images/generations"},
            "文生图 进行中",
        )
        queued_image = self.service.create_call(
            {"status": "queued", "stage": "getting_account", "endpoint": "/v1/images/generations"},
            "文生图",
        )
        text_item = self.service.create_call(
            {"status": "running", "endpoint": "/v1/responses"},
            "文本生成 进行中",
        )

        recovered = self.service.recover_orphaned_image_tasks()

        self.assertEqual(recovered, 2)
        running_log = self.service.get_by_id(running_image["id"])
        queued_log = self.service.get_by_id(queued_image["id"])
        text_log = self.service.get_by_id(text_item["id"])
        self.assertEqual(running_log["detail"]["status"], "stopped")
        self.assertEqual(queued_log["detail"]["status"], "stopped")
        self.assertEqual(queued_log["detail"]["stage"], "stopped")
        self.assertEqual(queued_log["detail"]["completion_reason"], "service_restarted")
        self.assertEqual(text_log["detail"]["status"], "running")

    def test_image_task_context_creates_one_queued_log_with_batch_metadata(self) -> None:
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
        self.assertEqual(item["detail"]["status"], "queued")
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

        selection = mock.Mock(access_token="token-1", model="gpt-image-2", waited_seconds=0.01)
        with mock.patch.object(conversation.account_service, "get_available_access_token_with_fallback", return_value=selection) as select, \
             mock.patch.object(conversation.account_service, "get_account", side_effect=get_account), \
             mock.patch.object(conversation.account_service, "mark_image_result") as mark:
            with self.assertRaises(conversation.ImageGenerationStopped):
                conversation._generate_single_image_with_context(request, 1, 1, context)

        self.assertIs(select.call_args.kwargs["cancel_event"], event)
        self.assertIsNone(select.call_args.kwargs["source_type"])
        self.assertNotIn("codex", select.call_args.kwargs["excluded_source_types"])
        mark.assert_called_once_with("token-1", False)

    def test_account_assignment_moves_queued_log_to_running(self) -> None:
        event = self.registry.register("task-1")
        context = ImageTaskLogContext("task-1", "batch-1", 1, 1, event)
        request = conversation.ConversationRequest(model="gpt-image-2", prompt="draw")
        output = conversation.ImageOutput(kind="result", model="gpt-image-2", index=1, total=1, data=[{"url": "/one.png"}])

        with (
            mock.patch.object(
                conversation.account_service,
                "get_available_access_token_with_fallback",
                return_value=mock.Mock(access_token="token-1", model="gpt-image-2", waited_seconds=0.01),
            ),
            mock.patch.object(conversation.account_service, "get_account", return_value={"email": "a@example.test"}),
            mock.patch.object(conversation.account_service, "mark_image_result"),
            mock.patch.object(conversation, "OpenAIBackendAPI"),
            mock.patch.object(conversation, "stream_image_outputs", return_value=iter([output])),
            mock.patch.object(conversation, "_update_image_task_log") as update_log,
        ):
            result = conversation._generate_single_image_with_context(request, 1, 1, context)

        self.assertEqual(result, [output])
        self.assertTrue(any(
            call.kwargs.get("status") == "running"
            and call.kwargs.get("stage") == "generating"
            and call.kwargs.get("account_email") == "a@example.test"
            for call in update_log.call_args_list
        ))

    def test_queue_timeout_uses_codex_model_without_mutating_shared_request(self) -> None:
        event = self.registry.register("task-1")
        context = ImageTaskLogContext("task-1", "batch-1", 1, 2, event)
        request = conversation.ConversationRequest(model="gpt-image-2", prompt="draw")
        output = conversation.ImageOutput(
            kind="result",
            model="codex-gpt-image-2",
            index=1,
            total=2,
            data=[{"url": "/one.png"}],
        )
        selection = mock.Mock(
            access_token="token-codex",
            model="codex-gpt-image-2",
            waited_seconds=10.25,
        )

        with (
            mock.patch.object(conversation.account_service, "get_available_access_token_with_fallback", return_value=selection),
            mock.patch.object(conversation.account_service, "get_account", return_value={"email": "codex@example.test"}),
            mock.patch.object(conversation.account_service, "mark_image_result"),
            mock.patch.object(conversation, "OpenAIBackendAPI"),
            mock.patch.object(conversation, "stream_codex_image_outputs", return_value=iter([output])) as codex_stream,
            mock.patch.object(conversation, "stream_image_outputs") as regular_stream,
            mock.patch.object(conversation, "_update_image_task_log") as update_log,
        ):
            result = conversation._generate_single_image_with_context(request, 1, 2, context)

        self.assertEqual(result, [output])
        self.assertEqual(request.model, "gpt-image-2")
        self.assertEqual(codex_stream.call_args.args[1].model, "codex-gpt-image-2")
        regular_stream.assert_not_called()
        self.assertTrue(any(
            call.kwargs.get("requested_model") == "gpt-image-2"
            and call.kwargs.get("effective_model") == "codex-gpt-image-2"
            and call.kwargs.get("fallback_reason") == "queue_wait_timeout"
            and call.kwargs.get("queue_wait_ms") == 10250
            for call in update_log.call_args_list
        ))

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
