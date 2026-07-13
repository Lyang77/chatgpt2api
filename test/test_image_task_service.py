from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from services.image_task_service import ImageTaskService
from services.openai_backend_api import ImagePollTimeoutError


OWNER = {"id": "owner-1", "name": "Owner", "role": "admin"}
OTHER_OWNER = {"id": "owner-2", "name": "Other", "role": "user"}


def wait_for_task(service: ImageTaskService, identity: dict[str, object], task_id: str, status: str, timeout: float = 2.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        result = service.list_tasks(identity, [task_id])
        last = (result.get("items") or [None])[0]
        if last and last.get("status") == status:
            return last
        time.sleep(0.02)
    raise AssertionError(f"task {task_id} did not reach {status}, last={last}")


class ImageTaskServiceTests(unittest.TestCase):
    def make_service(self, path: Path, handler=None) -> ImageTaskService:
        return ImageTaskService(
            path,
            generation_handler=handler or (lambda _payload: {"data": [{"url": "http://example.test/image.png"}]}),
            edit_handler=handler or (lambda _payload: {"data": [{"url": "http://example.test/edit.png"}]}),
            retention_days_getter=lambda: 30,
        )

    def test_duplicate_submit_uses_existing_task(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            calls = 0

            def handler(_payload):
                nonlocal calls
                calls += 1
                time.sleep(0.05)
                return {"data": [{"url": "http://example.test/image.png"}]}

            service = self.make_service(Path(tmp_dir) / "image_tasks.json", handler)
            first = service.submit_generation(
                OWNER,
                client_task_id="task-1",
                prompt="cat",
                model="gpt-image-2",
                size=None,
                base_url="http://local.test",
            )
            second = service.submit_generation(
                OWNER,
                client_task_id="task-1",
                prompt="cat",
                model="gpt-image-2",
                size=None,
                base_url="http://local.test",
            )

            self.assertEqual(first["id"], "task-1")
            self.assertEqual(second["id"], "task-1")
            task = wait_for_task(service, OWNER, "task-1", "success")
            self.assertEqual(task["data"][0]["url"], "http://example.test/image.png")
            self.assertEqual(calls, 1)

    def test_different_owner_cannot_query_task(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = self.make_service(Path(tmp_dir) / "image_tasks.json")
            service.submit_generation(
                OWNER,
                client_task_id="private-task",
                prompt="cat",
                model="gpt-image-2",
                size=None,
                base_url="http://local.test",
            )

            wait_for_task(service, OWNER, "private-task", "success")
            result = service.list_tasks(OTHER_OWNER, ["private-task"])

            self.assertEqual(result["items"], [])
            self.assertEqual(result["missing_ids"], ["private-task"])

    def test_success_task_persists_to_new_service_instance(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "image_tasks.json"
            service = self.make_service(path)
            service.submit_generation(
                OWNER,
                client_task_id="persisted-task",
                prompt="cat",
                model="gpt-image-2",
                size=None,
                base_url="http://local.test",
            )
            wait_for_task(service, OWNER, "persisted-task", "success")

            reloaded = self.make_service(path)
            result = reloaded.list_tasks(OWNER, ["persisted-task"])

            self.assertEqual(result["missing_ids"], [])
            self.assertEqual(result["items"][0]["status"], "success")
            self.assertEqual(result["items"][0]["data"][0]["url"], "http://example.test/image.png")

    def test_startup_marks_unfinished_tasks_as_error(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "image_tasks.json"
            path.write_text(
                json.dumps(
                    {
                        "tasks": [
                            {
                                "id": "queued-task",
                                "owner_id": "owner-1",
                                "status": "queued",
                                "mode": "generate",
                                "model": "gpt-image-2",
                                "created_at": "2099-01-01 00:00:00",
                                "updated_at": "2099-01-01 00:00:00",
                            },
                            {
                                "id": "running-task",
                                "owner_id": "owner-1",
                                "status": "running",
                                "mode": "generate",
                                "model": "gpt-image-2",
                                "created_at": "2099-01-01 00:00:00",
                                "updated_at": "2099-01-01 00:00:00",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            service = self.make_service(path)
            result = service.list_tasks(OWNER, ["queued-task", "running-task"])

            self.assertEqual([item["status"] for item in result["items"]], ["error", "error"])
            self.assertTrue(all("已中断" in item.get("error", "") for item in result["items"]))

    def test_running_task_exposes_incremental_deduplicated_results(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            first_reported = threading.Event()
            release_handler = threading.Event()
            captured_payload = {}

            def handler(payload):
                captured_payload.update(payload)
                payload["image_result_callback"]([{"url": "http://example.test/one.png"}])
                first_reported.set()
                release_handler.wait(1)
                payload["image_result_callback"]([
                    {"url": "http://example.test/one.png"},
                    {"url": "http://example.test/two.png"},
                ])
                return {
                    "data": [
                        {"url": "http://example.test/one.png"},
                        {"url": "http://example.test/two.png"},
                    ],
                    "_completion_reason": "upstream_completed",
                }

            service = self.make_service(Path(tmp_dir) / "image_tasks.json", handler)
            service.submit_generation(
                OWNER,
                client_task_id="multi-task",
                prompt="variants",
                model="gpt-image-2",
                size=None,
                base_url="http://local.test",
            )
            self.assertTrue(first_reported.wait(1))

            running = service.list_tasks(OWNER, ["multi-task"])["items"][0]
            self.assertEqual(running["status"], "running")
            self.assertEqual(running["data"], [{"url": "http://example.test/one.png"}])
            self.assertEqual(running["actual_image_count"], 1)
            self.assertNotIn("n", captured_payload)
            self.assertTrue(captured_payload["wait_for_image_terminal"])

            release_handler.set()
            completed = wait_for_task(service, OWNER, "multi-task", "success")
            self.assertEqual(len(completed["data"]), 2)
            self.assertEqual(completed["actual_image_count"], 2)
            self.assertEqual(completed["completion_reason"], "upstream_completed")

    def test_timeout_with_incremental_result_is_success(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            def handler(payload):
                payload["image_result_callback"]([{"url": "http://example.test/partial.png"}])
                raise ImagePollTimeoutError("timed out")

            service = self.make_service(Path(tmp_dir) / "image_tasks.json", handler)
            service.submit_generation(
                OWNER,
                client_task_id="partial-task",
                prompt="variants",
                model="gpt-image-2",
                size=None,
                base_url="http://local.test",
            )

            completed = wait_for_task(service, OWNER, "partial-task", "success")
            self.assertEqual(completed["actual_image_count"], 1)
            self.assertEqual(completed["completion_reason"], "timeout_with_results")

    def test_resume_poll_waits_for_terminal_and_reports_all_results(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = self.make_service(Path(tmp_dir) / "image_tasks.json")
            key = "owner-1:resume-task"
            with service._lock:
                service._tasks[key] = {
                    "id": "resume-task",
                    "owner_id": "owner-1",
                    "status": "running",
                    "mode": "generate",
                    "model": "gpt-image-2",
                    "quality": "auto",
                    "data": [],
                    "created_at": "2026-07-13 00:00:00",
                    "updated_at": "2026-07-13 00:00:00",
                }

            backend = mock.Mock()
            backend.last_image_completion_reason = "upstream_completed"
            backend._resolve_image_urls.side_effect = (
                lambda _conversation_id, file_ids, _sediment_ids: [
                    f"https://files.test/{file_id}.png" for file_id in file_ids
                ]
            )
            backend.download_image_bytes.side_effect = (
                lambda urls: [url.encode("utf-8") for url in urls]
            )

            def poll(_conversation_id, _timeout, *, wait_for_terminal, result_ids_callback):
                self.assertTrue(wait_for_terminal)
                result_ids_callback(["one"], [])
                result_ids_callback(["one", "two"], [])
                return ["one", "two"], []

            backend._poll_image_results.side_effect = poll

            with (
                mock.patch("services.openai_backend_api.OpenAIBackendAPI", return_value=backend),
                mock.patch.object(service, "_log_call") as log_call,
            ):
                service._run_resume_poll(
                    key,
                    "conv-1",
                    30,
                    OWNER,
                    "generate",
                    "gpt-image-2",
                )

            completed = service.list_tasks(OWNER, ["resume-task"])["items"][0]
            self.assertEqual(completed["status"], "success")
            self.assertEqual(completed["actual_image_count"], 2)
            self.assertEqual(completed["completion_reason"], "upstream_completed")
            self.assertEqual(log_call.call_args.kwargs["actual_image_count"], 2)


if __name__ == "__main__":
    unittest.main()
