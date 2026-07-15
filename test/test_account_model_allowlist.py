from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.accounts as accounts_module
from services.account_service import AccountModelUnavailableError, AccountService
from services.storage.json_storage import JSONStorageBackend
from utils.helper import CODEX_TEXT_MODEL, is_codex_text_model


class AccountModelAllowlistTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.service = AccountService(JSONStorageBackend(Path(self.temp_dir.name) / "accounts.json"))
        self.log_patcher = mock.patch("services.account_service.log_service")
        self.log_patcher.start()
        self.addCleanup(self.log_patcher.stop)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_normalizes_allowed_models_on_account_import(self) -> None:
        self.service.add_account_items([
            {
                "access_token": "token-a",
                "allowed_models": [" GPT-5-3 ", "gpt-5-3", "", "gpt-5-5"],
            },
        ])

        account = self.service.get_account("token-a")

        self.assertEqual(account.get("allowed_models"), ["gpt-5-3", "gpt-5-5"])

    def test_text_selection_uses_exact_account_model_allowlist(self) -> None:
        self.service.add_account_items([
            {"access_token": "token-a", "status": "正常", "allowed_models": ["gpt-5-3"]},
            {"access_token": "token-b", "status": "正常", "allowed_models": ["gpt-5-5"]},
        ])
        self.service.refresh_access_token = lambda token, **_: token  # type: ignore[method-assign]

        self.assertEqual(self.service.get_text_access_token("gpt-5-5"), "token-b")

    def test_codex_text_model_uses_exact_external_name(self) -> None:
        self.assertEqual(CODEX_TEXT_MODEL, "gpt-5.5")
        self.assertTrue(is_codex_text_model("gpt-5.5"))
        self.assertFalse(is_codex_text_model("gpt-5-5"))

    def test_codex_text_models_define_exact_supported_set(self) -> None:
        try:
            from utils.helper import CODEX_TEXT_MODELS
        except ImportError:
            self.fail("utils.helper.CODEX_TEXT_MODELS is not implemented")

        self.assertEqual(
            CODEX_TEXT_MODELS,
            ("gpt-5.5", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.6-sol"),
        )
        for model in CODEX_TEXT_MODELS:
            with self.subTest(model=model):
                self.assertTrue(is_codex_text_model(model))

    def test_codex_text_model_recognition_rejects_unknown_variants(self) -> None:
        for model in (
            "gpt-5-5",
            "gpt-5.6",
            "gpt-5.6-sol-pro",
            "GPT-5.6-SOL",
            " gpt-5.6-sol ",
        ):
            with self.subTest(model=model):
                self.assertFalse(is_codex_text_model(model))

    def test_text_selection_can_require_codex_source(self) -> None:
        self.service.add_account_items([
            {"access_token": "token-web", "source_type": "web", "allowed_models": ["gpt-5.5"]},
            {"access_token": "token-codex", "source_type": "codex", "allowed_models": ["gpt-5.5"]},
        ])
        self.service.refresh_access_token = lambda token, **_: token  # type: ignore[method-assign]

        token = self.service.get_text_access_token("gpt-5.5", source_type="codex")

        self.assertEqual(token, "token-codex")

    def test_text_selection_rejects_web_fallback_for_codex_source(self) -> None:
        self.service.add_account_items([
            {"access_token": "token-web", "source_type": "web", "allowed_models": ["gpt-5.5"]},
        ])
        self.service.refresh_access_token = lambda token, **_: token  # type: ignore[method-assign]

        with self.assertRaisesRegex(RuntimeError, "no available account supports model gpt-5.5"):
            self.service.get_text_access_token("gpt-5.5", source_type="codex")

    def test_auto_uses_only_an_unrestricted_account(self) -> None:
        self.service.add_account_items([
            {"access_token": "token-a", "status": "正常", "allowed_models": ["gpt-5-3"]},
            {"access_token": "token-b", "status": "正常"},
        ])
        self.service.refresh_access_token = lambda token, **_: token  # type: ignore[method-assign]

        self.assertEqual(self.service.get_text_access_token("auto"), "token-b")

    def test_text_selection_rejects_an_unconfigured_model(self) -> None:
        self.service.add_account_items([
            {"access_token": "token-a", "status": "正常", "allowed_models": ["gpt-5-3"]},
        ])

        with self.assertRaisesRegex(RuntimeError, "no available account supports model gpt-5-5"):
            self.service.get_text_access_token("gpt-5-5")

    def test_image_selection_uses_account_model_allowlist(self) -> None:
        self.service.add_account_items([
            {
                "access_token": "token-web",
                "status": "正常",
                "quota": 2,
                "allowed_models": ["gpt-image-2"],
            },
            {
                "access_token": "token-codex",
                "type": "Plus",
                "source_type": "codex",
                "status": "正常",
                "quota": 2,
                "allowed_models": ["codex-gpt-image-2"],
            },
        ])
        self.service.fetch_remote_info = lambda token, event="": self.service.get_account(token)  # type: ignore[method-assign]

        token = self.service.get_available_access_token(model="gpt-image-2")

        self.addCleanup(self.service.release_image_slot, token)
        self.assertEqual(token, "token-web")

    def test_image_selection_reports_quota_unavailable_after_remote_refresh(self) -> None:
        self.service.add_account_items([
            {
                "access_token": "token-image",
                "status": "正常",
                "quota": 1,
                "allowed_models": ["gpt-image-2"],
            },
        ])
        self.service.fetch_remote_info = lambda token, event="": {
            **(self.service.get_account(token) or {}),
            "quota": 0,
        }  # type: ignore[method-assign]

        with self.assertRaisesRegex(RuntimeError, "no available image quota") as raised:
            self.service.get_available_access_token(model="gpt-image-2")

        self.assertNotIsInstance(raised.exception, AccountModelUnavailableError)


class AccountModelAllowlistApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.service = AccountService(JSONStorageBackend(Path(self.temp_dir.name) / "accounts.json"))
        self.log_patcher = mock.patch("services.account_service.log_service")
        self.log_patcher.start()
        self.addCleanup(self.log_patcher.stop)
        self.service.add_account_items([{"access_token": "token-a", "status": "正常"}])
        self.patchers = [
            mock.patch.object(accounts_module, "account_service", self.service),
            mock.patch.object(accounts_module, "require_admin", lambda _authorization: {"role": "admin"}),
        ]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        app = FastAPI()
        app.include_router(accounts_module.create_router())
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_account_update_persists_allowed_models(self) -> None:
        response = self.client.post(
            "/api/accounts/update",
            headers={"Authorization": "Bearer chatgpt2api"},
            json={
                "access_token": "token-a",
                "allowed_models": [" GPT-5-3 ", "gpt-5-3", "gpt-5-5"],
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["item"]["allowed_models"], ["gpt-5-3", "gpt-5-5"])

    def test_account_update_persists_image_max_inflight(self) -> None:
        response = self.client.post(
            "/api/accounts/update",
            headers={"Authorization": "Bearer chatgpt2api"},
            json={
                "access_token": "token-a",
                "image_max_inflight": 5,
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["item"]["image_max_inflight"], 5)

    def test_account_update_rejects_invalid_image_max_inflight(self) -> None:
        response = self.client.post(
            "/api/accounts/update",
            headers={"Authorization": "Bearer chatgpt2api"},
            json={
                "access_token": "token-a",
                "image_max_inflight": 0,
            },
        )

        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.service.get_account("token-a")["image_max_inflight"], 3)


if __name__ == "__main__":
    unittest.main()
