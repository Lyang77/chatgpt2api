from __future__ import annotations

import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.system as system_module


AUTH_HEADERS = {"Authorization": "Bearer chatgpt2api"}


class _FakeLogService:
    def __init__(self) -> None:
        self.list_kwargs: dict[str, object] | None = None
        self.detail_id = ""
        self.delete_ids: list[str] | None = None

    def list(self, **kwargs: object) -> dict[str, object]:
        self.list_kwargs = kwargs
        return {"items": [], "total": 0, "page": kwargs["page"], "page_size": kwargs["page_size"], "total_pages": 0}

    def get_by_id(self, log_id: str) -> dict[str, object] | None:
        self.detail_id = log_id
        return {"id": log_id, "detail": {"response_text": "full detail"}}

    def delete(self, ids: list[str]) -> dict[str, int]:
        self.delete_ids = ids
        return {"removed": len(ids)}

    def request_stop(self, log_id: str) -> tuple[bool, dict[str, object] | None]:
        self.stop_id = log_id
        return True, {"id": log_id, "detail": {"status": "running", "stop_requested_at": "2026-07-10 12:00:00"}}


class LogsApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.log_service = _FakeLogService()
        self.calls: list[tuple[object, tuple[object, ...], dict[str, object]]] = []

        async def run_in_threadpool(func, *args, **kwargs):  # type: ignore[no-untyped-def]
            self.calls.append((func, args, kwargs))
            return func(*args, **kwargs)

        self.patchers = [
            mock.patch.object(system_module, "log_service", self.log_service),
            mock.patch.object(system_module, "require_admin", lambda _authorization: {"role": "admin"}),
            mock.patch.object(system_module, "run_in_threadpool", run_in_threadpool),
        ]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        app = FastAPI()
        app.include_router(system_module.create_router("9.9.9-test"))
        self.client = TestClient(app)

    def test_log_endpoints_use_threadpool_and_keep_request_contract(self) -> None:
        listed = self.client.get(
            "/api/logs",
            headers=AUTH_HEADERS,
            params={"page": 0, "page_size": 201, "key_name": " alpha ", "status": " success "},
        )
        detail = self.client.get("/api/logs/log-1", headers=AUTH_HEADERS)
        deleted = self.client.post("/api/logs/delete", headers=AUTH_HEADERS, json={"ids": ["log-1", "log-2"]})

        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertEqual(
            self.log_service.list_kwargs,
            {
                "type": "",
                "start_date": "",
                "end_date": "",
                "page": 1,
                "page_size": 20,
                "key_name": "alpha",
                "account_email": "",
                "status": "success",
                "summary": "",
                "model": "",
                "endpoint": "",
                "batch_id": "",
            },
        )
        self.assertEqual(self.log_service.detail_id, "log-1")
        self.assertEqual(self.log_service.delete_ids, ["log-1", "log-2"])
        self.assertEqual([call[0] for call in self.calls], [
            self.log_service.list,
            self.log_service.get_by_id,
            self.log_service.delete,
        ])

    def test_stop_running_log_uses_threadpool(self) -> None:
        response = self.client.post("/api/logs/log-running/stop", headers=AUTH_HEADERS)

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["stopped"], True)
        self.assertEqual(self.log_service.stop_id, "log-running")
        self.assertEqual(self.calls[-1][0], self.log_service.request_stop)


if __name__ == "__main__":
    unittest.main()
