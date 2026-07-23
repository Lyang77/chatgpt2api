from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from services.editable_file_task_service import EditableFileTaskService


IDENTITY = {"id": "owner-1", "name": "tester", "role": "user"}


class EditableFileTaskServiceLogTests(unittest.TestCase):
    def make_service(self, tmp_dir: str) -> EditableFileTaskService:
        return EditableFileTaskService(Path(tmp_dir) / "editable_tasks.json")

    def seed_task(self, service: EditableFileTaskService, task_id: str, kind: str) -> str:
        key = f"owner-1:{task_id}"
        with service._lock:
            service._tasks[key] = {
                "id": task_id,
                "owner_id": "owner-1",
                "status": "queued",
                "kind": kind,
                "created_at": "2026-07-23 00:00:00",
                "updated_at": "2026-07-23 00:00:00",
            }
        return key

    def test_ppt_success_log_records_safe_request_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = self.make_service(tmp_dir)
            key = self.seed_task(service, "deck-1", "ppt")
            backend = mock.Mock()
            backend.export_ppt_zip.return_value = SimpleNamespace(
                conversation_id="conv-1",
                primary_path=Path(tmp_dir) / "deck.pptx",
                zip_path=Path(tmp_dir) / "deck.zip",
            )

            with (
                mock.patch("services.editable_file_task_service._editable_access_token", return_value="token"),
                mock.patch("services.editable_file_task_service.account_service.get_account", return_value={"email": "a@test.local"}),
                mock.patch("services.editable_file_task_service.account_service.mark_text_used"),
                mock.patch("services.editable_file_task_service.OpenAIBackendAPI", return_value=backend),
                mock.patch("services.editable_file_task_service._file_url", side_effect=["/files/deck.pptx", "/files/deck.zip"]),
                mock.patch("services.editable_file_task_service.log_service.add") as add_log,
            ):
                service._run_task(key, "ppt", "make slides", ["data:image/png;base64,SECRET"], IDENTITY, "")

            detail = add_log.call_args.args[2]
            self.assertEqual(detail["request_meta"], {
                "prompt_chars": len("make slides"),
                "client_task_id": "deck-1",
                "reference_image_count": 1,
            })
            self.assertNotIn("SECRET", repr(detail["request_meta"]))

    def test_psd_failure_log_records_request_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = self.make_service(tmp_dir)
            key = self.seed_task(service, "psd-1", "psd")

            with mock.patch("services.editable_file_task_service.log_service.add") as add_log:
                service._run_task(key, "psd", "make layers", [], IDENTITY, "")

            detail = add_log.call_args.args[2]
            self.assertEqual(detail["status"], "failed")
            self.assertEqual(detail["request_meta"], {
                "prompt_chars": len("make layers"),
                "client_task_id": "psd-1",
                "reference_image_count": 0,
            })


if __name__ == "__main__":
    unittest.main()
