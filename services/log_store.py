from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any


class SQLiteLogStore:
    """SQLite persistence for system logs with one-time JSONL import."""

    def __init__(self, path: Path, legacy_path: Path):
        self.path = path
        self.legacy_path = legacy_path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            with connection:
                connection.executescript("""
                    CREATE TABLE IF NOT EXISTS system_log (
                        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                        id TEXT NOT NULL UNIQUE,
                        log_time TEXT NOT NULL,
                        log_type TEXT NOT NULL,
                        summary TEXT NOT NULL,
                        key_name TEXT NOT NULL DEFAULT '',
                        account_email TEXT NOT NULL DEFAULT '',
                        status TEXT NOT NULL DEFAULT '',
                        detail_json TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_system_log_time
                        ON system_log(log_time DESC, sequence DESC);
                    CREATE INDEX IF NOT EXISTS idx_system_log_type_time
                        ON system_log(log_type, log_time DESC, sequence DESC);
                    CREATE INDEX IF NOT EXISTS idx_system_log_status_time
                        ON system_log(status, log_time DESC, sequence DESC);
                    CREATE INDEX IF NOT EXISTS idx_system_log_account_time
                        ON system_log(account_email, log_time DESC, sequence DESC);
                    CREATE INDEX IF NOT EXISTS idx_system_log_key_time
                        ON system_log(key_name, log_time DESC, sequence DESC);
                    CREATE TABLE IF NOT EXISTS log_store_metadata (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    );
                """)
                migrated = connection.execute(
                    "SELECT 1 FROM log_store_metadata WHERE key = 'legacy_jsonl_v1'"
                ).fetchone()
                if migrated is None:
                    self._import_legacy_jsonl(connection)
                    connection.execute(
                        "INSERT INTO log_store_metadata (key, value) VALUES ('legacy_jsonl_v1', 'completed')"
                    )
        finally:
            connection.close()

    @staticmethod
    def _legacy_id(raw_line: str, line_number: int) -> str:
        payload = f"{line_number}:{raw_line}".encode("utf-8", errors="ignore")
        return hashlib.sha1(payload).hexdigest()[:24]

    def _import_legacy_jsonl(self, connection: sqlite3.Connection) -> None:
        if not self.legacy_path.exists():
            return
        with self.legacy_path.open("r", encoding="utf-8") as source:
            for line_number, raw_line in enumerate(source):
                line = raw_line.rstrip("\r\n")
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except Exception:
                    continue
                if not isinstance(item, dict):
                    continue
                normalized = self._normalize_item(item, fallback_id=self._legacy_id(line, line_number))
                self._insert(connection, normalized)

    @staticmethod
    def _normalize_item(item: dict[str, Any], *, fallback_id: str = "") -> dict[str, Any]:
        detail = item.get("detail")
        return {
            "id": str(item.get("id") or fallback_id),
            "time": str(item.get("time") or ""),
            "type": str(item.get("type") or ""),
            "summary": str(item.get("summary") or ""),
            "detail": dict(detail) if isinstance(detail, dict) else {},
        }

    @staticmethod
    def _insert(connection: sqlite3.Connection, item: dict[str, Any]) -> None:
        detail = item["detail"]
        connection.execute(
            """
            INSERT OR IGNORE INTO system_log (
                id, log_time, log_type, summary, key_name, account_email, status, detail_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item["id"],
                item["time"],
                item["type"],
                item["summary"],
                str(detail.get("key_name") or ""),
                str(detail.get("account_email") or ""),
                str(detail.get("status") or ""),
                json.dumps(detail, ensure_ascii=False, separators=(",", ":")),
            ),
        )

    @staticmethod
    def _from_row(row: sqlite3.Row) -> dict[str, Any]:
        try:
            detail = json.loads(str(row["detail_json"] or ""))
        except Exception:
            detail = {}
        return {
            "id": str(row["id"]),
            "time": str(row["log_time"]),
            "type": str(row["log_type"]),
            "summary": str(row["summary"]),
            "detail": detail if isinstance(detail, dict) else {},
        }

    @staticmethod
    def _like_value(value: str) -> str:
        return "%" + value.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"

    def append(self, item: dict[str, Any]) -> None:
        normalized = self._normalize_item(item)
        if not normalized["id"]:
            raise ValueError("log id is required")
        connection = self._connect()
        try:
            with connection:
                self._insert(connection, normalized)
        finally:
            connection.close()

    def update(self, log_id: str, item: dict[str, Any]) -> dict[str, Any] | None:
        normalized = self._normalize_item(item)
        target_id = str(log_id or "").strip()
        if not target_id:
            return None
        detail = normalized["detail"]
        connection = self._connect()
        try:
            with connection:
                cursor = connection.execute(
                    """
                    UPDATE system_log
                    SET log_time = ?, log_type = ?, summary = ?, key_name = ?,
                        account_email = ?, status = ?, detail_json = ?
                    WHERE id = ?
                    """,
                    (
                        normalized["time"],
                        normalized["type"],
                        normalized["summary"],
                        str(detail.get("key_name") or ""),
                        str(detail.get("account_email") or ""),
                        str(detail.get("status") or ""),
                        json.dumps(detail, ensure_ascii=False, separators=(",", ":")),
                        target_id,
                    ),
                )
                if not cursor.rowcount:
                    return None
        finally:
            connection.close()
        return self.get_by_id(target_id)

    def _list_image_subtasks_by_statuses(
        self,
        statuses: tuple[str, ...],
        account_email: str = "",
    ) -> list[dict[str, Any]]:
        clauses = ["log_type = ?", f"status IN ({','.join('?' for _ in statuses)})"]
        params: list[object] = ["call", *statuses]
        email = str(account_email or "").strip()
        if email:
            clauses.append("account_email = ?")
            params.append(email)
        connection = self._connect()
        try:
            rows = connection.execute(
                f"""
                SELECT id, log_time, log_type, summary, detail_json
                FROM system_log
                WHERE {' AND '.join(clauses)}
                ORDER BY log_time DESC, sequence DESC
                """,
                params,
            ).fetchall()
        finally:
            connection.close()
        return [
            item
            for row in rows
            if str((item := self._from_row(row)).get("detail", {}).get("endpoint") or "").startswith("/v1/images/")
        ]

    def list_running_image_subtasks(self, account_email: str = "") -> list[dict[str, Any]]:
        return self._list_image_subtasks_by_statuses(("running",), account_email)

    def list_unfinished_image_subtasks(self, account_email: str = "") -> list[dict[str, Any]]:
        return self._list_image_subtasks_by_statuses(("queued", "running"), account_email)

    def list(
        self,
        *,
        type: str = "",
        start_date: str = "",
        end_date: str = "",
        page: int = 1,
        page_size: int = 20,
        key_name: str = "",
        account_email: str = "",
        status: str = "",
        summary: str = "",
        model: str = "",
        endpoint: str = "",
        batch_id: str = "",
    ) -> dict[str, Any]:
        clauses: list[str] = []
        params: list[object] = []
        if type:
            clauses.append("log_type = ?")
            params.append(type)
        if start_date:
            clauses.append("log_time >= ?")
            params.append(f"{start_date} 00:00:00")
        if end_date:
            clauses.append("log_time <= ?")
            params.append(f"{end_date} 23:59:59")
        for column, value in (("key_name", key_name), ("account_email", account_email), ("summary", summary)):
            if value:
                clauses.append(f"LOWER({column}) LIKE ? ESCAPE '\\'")
                params.append(self._like_value(value))
        if status:
            clauses.append("status = ?")
            params.append(status)
        for key, value in (("model", model), ("endpoint", endpoint), ("batch_id", batch_id)):
            if value:
                clauses.append("detail_json LIKE ?")
                params.append(f'%"{key}":{json.dumps(value, ensure_ascii=False)}%')

        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        connection = self._connect()
        try:
            total = int(connection.execute(f"SELECT COUNT(*) FROM system_log{where}", params).fetchone()[0])
            offset = (page - 1) * page_size
            rows = connection.execute(
                f"""
                SELECT id, log_time, log_type, summary, detail_json
                FROM system_log{where}
                ORDER BY log_time DESC, sequence DESC
                LIMIT ? OFFSET ?
                """,
                [*params, page_size, offset],
            ).fetchall()
        finally:
            connection.close()
        total_pages = (total + page_size - 1) // page_size if total > 0 else 0
        return {
            "items": [self._from_row(row) for row in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
        }

    def get_by_id(self, log_id: str) -> dict[str, Any] | None:
        target_id = str(log_id or "").strip()
        if not target_id:
            return None
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT id, log_time, log_type, summary, detail_json FROM system_log WHERE id = ?",
                (target_id,),
            ).fetchone()
        finally:
            connection.close()
        return self._from_row(row) if row is not None else None

    def delete(self, ids: list[str]) -> int:
        target_ids = sorted({str(item or "").strip() for item in ids if str(item or "").strip()})
        if not target_ids:
            return 0
        connection = self._connect()
        try:
            with connection:
                cursor = connection.execute(
                    f"DELETE FROM system_log WHERE id IN ({','.join('?' for _ in target_ids)})",
                    target_ids,
                )
                return cursor.rowcount
        finally:
            connection.close()
