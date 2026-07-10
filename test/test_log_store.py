from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from services.log_store import SQLiteLogStore


class SQLiteLogStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)
        self.legacy_path = self.data_dir / "logs.jsonl"
        self.database_path = self.data_dir / "logs.db"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @staticmethod
    def _item(
        log_id: str,
        log_time: str,
        *,
        type: str = "call",
        summary: str = "completed",
        detail: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return {
            "id": log_id,
            "time": log_time,
            "type": type,
            "summary": summary,
            "detail": detail or {},
        }

    def test_migrates_legacy_jsonl_once_and_keeps_legacy_ids(self) -> None:
        existing = self._item("saved-id", "2026-07-10 09:00:00")
        missing_id = self._item("", "2026-07-10 10:00:00", summary="legacy id")
        raw_missing_id = json.dumps(missing_id, ensure_ascii=False, separators=(",", ":"))
        self.legacy_path.write_text(
            "\n".join([json.dumps(existing, ensure_ascii=False), raw_missing_id, "not-json"]) + "\n",
            encoding="utf-8",
        )

        store = SQLiteLogStore(self.database_path, self.legacy_path)
        result = store.list(page=1, page_size=20)
        expected_legacy_id = hashlib.sha1(f"1:{raw_missing_id}".encode("utf-8")).hexdigest()[:24]

        self.assertEqual(result["total"], 2)
        self.assertEqual([item["id"] for item in result["items"]], [expected_legacy_id, "saved-id"])
        self.assertEqual(store.get_by_id(expected_legacy_id)["summary"], "legacy id")  # type: ignore[index]

        reopened = SQLiteLogStore(self.database_path, self.legacy_path)
        self.assertEqual(reopened.list(page=1, page_size=20)["total"], 2)

        self.assertEqual(reopened.delete([expected_legacy_id, "saved-id"]), 2)
        after_delete = SQLiteLogStore(self.database_path, self.legacy_path)
        self.assertEqual(after_delete.list(page=1, page_size=20)["total"], 0)

    def test_uses_sql_filters_pagination_and_delete(self) -> None:
        store = SQLiteLogStore(self.database_path, self.legacy_path)
        store.append(self._item(
            "first",
            "2026-07-08 09:00:00",
            summary="alpha complete",
            detail={"key_name": "Alpha Key", "account_email": "a@example.test", "status": "success"},
        ))
        store.append(self._item(
            "second",
            "2026-07-09 09:00:00",
            detail={"key_name": "Beta Key", "account_email": "b@example.test", "status": "failed"},
        ))
        store.append(self._item(
            "third",
            "2026-07-10 09:00:00",
            type="account",
            summary="alpha account",
            detail={"key_name": "Alpha Key", "account_email": "a@example.test", "status": "success"},
        ))

        filtered = store.list(
            type="call",
            start_date="2026-07-08",
            end_date="2026-07-09",
            key_name="beta",
            account_email="B@EXAMPLE",
            status="failed",
            summary="complete",
            page=1,
            page_size=1,
        )

        self.assertEqual(filtered["total"], 1)
        self.assertEqual(filtered["total_pages"], 1)
        self.assertEqual([item["id"] for item in filtered["items"]], ["second"])
        self.assertEqual(store.delete(["second", "missing"]), 1)
        self.assertIsNone(store.get_by_id("second"))


if __name__ == "__main__":
    unittest.main()
