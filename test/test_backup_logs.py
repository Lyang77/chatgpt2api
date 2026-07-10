from __future__ import annotations

import io
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from services.backup_service import BackupService


class _FakeStorage:
    def get_backend_info(self) -> dict[str, object]:
        return {"type": "json"}


class _FakeConfig:
    app_version = "test"

    def get_storage_backend(self) -> _FakeStorage:
        return _FakeStorage()


class BackupLogsTests(unittest.TestCase):
    def test_log_backup_includes_legacy_and_sqlite_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            (data_dir / "logs.jsonl").write_text('{"id":"legacy"}\n', encoding="utf-8")
            (data_dir / "logs.db").write_bytes(b"sqlite database")
            settings = {"include": {"logs": True}}

            with (
                mock.patch("services.backup_service.DATA_DIR", data_dir),
                mock.patch("services.backup_service.config", _FakeConfig()),
            ):
                payload = BackupService()._build_backup_archive(settings, trigger="manual")

            with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
                self.assertEqual(
                    {member.name for member in archive.getmembers()},
                    {"backup-metadata.json", "data/logs.jsonl", "data/logs.db"},
                )


if __name__ == "__main__":
    unittest.main()
