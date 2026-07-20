from __future__ import annotations

import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("CHATGPT2API_AUTH_KEY", "test-auth")

from services.account_service import AccountService
from services.auth_service import AuthService
from services.config import config
from services.openai_backend_api import InvalidAccessTokenError
from services.storage.json_storage import JSONStorageBackend
from utils.helper import anonymize_token, split_image_model


class AccountCapabilityTests(unittest.TestCase):
    def test_image_max_inflight_defaults_to_three(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = AccountService(JSONStorageBackend(Path(tmp_dir) / "accounts.json"))
            service.add_account_items([
                {"access_token": "token-default"},
                {"access_token": "token-invalid", "image_max_inflight": 0},
            ])

            self.assertEqual(service.get_account("token-default")["image_max_inflight"], 3)
            self.assertEqual(service.get_account("token-invalid")["image_max_inflight"], 3)

    def test_image_candidate_capacity_is_configured_per_account(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = AccountService(JSONStorageBackend(Path(tmp_dir) / "accounts.json"))
            service.add_account_items([
                {
                    "access_token": "token-one",
                    "status": "正常",
                    "quota": 5,
                    "image_max_inflight": 1,
                },
                {
                    "access_token": "token-two",
                    "status": "正常",
                    "quota": 5,
                    "image_max_inflight": 2,
                },
            ])
            service._image_inflight.update({"token-one": 1, "token-two": 1})

            with patch.dict(config.data, {"image_account_concurrency": 99}):
                candidates = service._list_available_candidate_tokens()

            self.assertEqual(candidates, ["token-two"])

    def test_image_request_waits_until_an_account_slot_is_released(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = AccountService(JSONStorageBackend(Path(tmp_dir) / "accounts.json"))
            service.add_account_items([
                {
                    "access_token": "token-one",
                    "status": "正常",
                    "quota": 5,
                    "image_max_inflight": 1,
                }
            ])
            service.fetch_remote_info = (
                lambda access_token, event="fetch_remote_info": service.get_account(access_token)
            )

            first_token = service.get_available_access_token()
            second_acquired = threading.Event()
            second_tokens: list[str] = []

            def acquire_second_token() -> None:
                token = service.get_available_access_token()
                second_tokens.append(token)
                second_acquired.set()
                service.release_image_slot(token)

            worker = threading.Thread(target=acquire_second_token, daemon=True)
            worker.start()
            self.assertFalse(second_acquired.wait(0.2))

            service.release_image_slot(first_token)

            self.assertTrue(second_acquired.wait(2.0))
            worker.join(timeout=2.0)
            self.assertEqual(second_tokens, ["token-one"])

    def test_cancelled_image_request_stops_waiting_for_an_account_slot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = AccountService(JSONStorageBackend(Path(tmp_dir) / "accounts.json"))
            service.add_account_items([
                {
                    "access_token": "token-one",
                    "status": "正常",
                    "quota": 5,
                    "image_max_inflight": 1,
                }
            ])
            service.fetch_remote_info = (
                lambda access_token, event="fetch_remote_info": service.get_account(access_token)
            )
            first_token = service.get_available_access_token()
            cancel_event = threading.Event()
            errors: list[BaseException] = []

            def acquire_cancelled_token() -> None:
                try:
                    service.get_available_access_token(cancel_event=cancel_event)
                except BaseException as exc:
                    errors.append(exc)

            worker = threading.Thread(target=acquire_cancelled_token, daemon=True)
            worker.start()
            self.assertFalse(cancel_event.wait(0.2))
            cancel_event.set()

            worker.join(timeout=2.0)
            service.release_image_slot(first_token)
            self.assertFalse(worker.is_alive())
            self.assertEqual(len(errors), 1)
            self.assertIsInstance(errors[0], InterruptedError)

    @staticmethod
    def _add_primary_and_codex_image_accounts(service: AccountService) -> None:
        service.add_account_items([
            {
                "access_token": "token-primary",
                "source_type": "password",
                "status": "正常",
                "quota": 5,
                "image_max_inflight": 1,
                "allowed_models": ["gpt-image-2"],
            },
            {
                "access_token": "token-codex",
                "source_type": "codex",
                "type": "Plus",
                "status": "正常",
                "quota": 5,
                "image_max_inflight": 1,
                "allowed_models": ["gpt-image-2", "codex-gpt-image-2"],
            },
        ])
        service.fetch_remote_info = (
            lambda access_token, event="fetch_remote_info": service.get_account(access_token)
        )

    def test_image_fallback_keeps_primary_route_before_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = AccountService(JSONStorageBackend(Path(tmp_dir) / "accounts.json"))
            self._add_primary_and_codex_image_accounts(service)
            held_token = service.get_available_access_token(
                source_type="password",
                model="gpt-image-2",
            )
            selections: list[object] = []

            worker = threading.Thread(
                target=lambda: selections.append(service.get_available_access_token_with_fallback(
                    model="gpt-image-2",
                    fallback_model="codex-gpt-image-2",
                    fallback_after_seconds=1.0,
                    excluded_source_types=("codex",),
                    fallback_source_type="codex",
                    fallback_plan_types=("plus", "team", "pro"),
                )),
                daemon=True,
            )
            worker.start()
            threading.Event().wait(0.05)
            service.release_image_slot(held_token)
            worker.join(timeout=2.0)

            self.assertFalse(worker.is_alive())
            self.assertEqual(len(selections), 1)
            self.assertEqual(selections[0].access_token, "token-primary")
            self.assertEqual(selections[0].model, "gpt-image-2")
            service.release_image_slot(selections[0].access_token)

    def test_image_fallback_uses_codex_route_after_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = AccountService(JSONStorageBackend(Path(tmp_dir) / "accounts.json"))
            self._add_primary_and_codex_image_accounts(service)
            held_token = service.get_available_access_token(
                source_type="password",
                model="gpt-image-2",
            )

            selection = service.get_available_access_token_with_fallback(
                model="gpt-image-2",
                fallback_model="codex-gpt-image-2",
                fallback_after_seconds=0.0,
                excluded_source_types=("codex",),
                fallback_source_type="codex",
                fallback_plan_types=("plus", "team", "pro"),
            )

            self.assertEqual(selection.access_token, "token-codex")
            self.assertEqual(selection.model, "codex-gpt-image-2")
            self.assertEqual(service._image_inflight, {"token-primary": 1, "token-codex": 1})
            service.release_image_slot(held_token)
            service.release_image_slot(selection.access_token)

    def test_image_fallback_wait_can_be_cancelled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = AccountService(JSONStorageBackend(Path(tmp_dir) / "accounts.json"))
            self._add_primary_and_codex_image_accounts(service)
            primary_token = service.get_available_access_token(
                source_type="password",
                model="gpt-image-2",
            )
            codex_token = service.get_available_access_token(
                source_type="codex",
                plan_types=("plus", "team", "pro"),
                model="codex-gpt-image-2",
            )
            cancel_event = threading.Event()
            errors: list[BaseException] = []

            def acquire_cancelled_token() -> None:
                try:
                    service.get_available_access_token_with_fallback(
                        model="gpt-image-2",
                        fallback_model="codex-gpt-image-2",
                        fallback_after_seconds=0.0,
                        excluded_source_types=("codex",),
                        fallback_source_type="codex",
                        fallback_plan_types=("plus", "team", "pro"),
                        cancel_event=cancel_event,
                    )
                except BaseException as exc:
                    errors.append(exc)

            worker = threading.Thread(
                target=acquire_cancelled_token,
                daemon=True,
            )
            worker.start()
            self.assertFalse(cancel_event.wait(0.05))
            cancel_event.set()
            worker.join(timeout=2.0)

            service.release_image_slot(primary_token)
            service.release_image_slot(codex_token)
            self.assertFalse(worker.is_alive())
            self.assertEqual(len(errors), 1)
            self.assertIsInstance(errors[0], InterruptedError)

    def test_image_accounts_require_positive_quota(self) -> None:
        self.assertFalse(
            AccountService._is_image_account_available(
                {"status": "限流", "quota": 1}
            )
        )
        self.assertFalse(
            AccountService._is_image_account_available(
                {"status": "正常", "quota": 0}
            )
        )
        self.assertTrue(AccountService._is_image_account_available({"status": "正常", "quota": 1}))

    def test_prolite_variants_are_normalized(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = AccountService(JSONStorageBackend(Path(tmp_dir) / "accounts.json"))
            self.assertEqual(service._normalize_account_type("prolite"), "ProLite")
            self.assertEqual(service._normalize_account_type("pro_lite"), "ProLite")

    def test_search_account_type_ignores_unrelated_scalar_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = AccountService(JSONStorageBackend(Path(tmp_dir) / "accounts.json"))
            self.assertIsNone(
                service._search_account_type(
                    {
                        "amr": ["pwd", "otp", "mfa"],
                        "chatgpt_compute_residency": "no_constraint",
                        "chatgpt_data_residency": "no_constraint",
                        "user_id": "user-I52GFfLGFM0dokFk2dBiKEBn",
                    }
                )
            )

    def test_mark_image_result_consumes_quota(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = AccountService(JSONStorageBackend(Path(tmp_dir) / "accounts.json"))
            service.add_accounts(["token-1"])
            service.update_account(
                "token-1",
                {
                    "status": "正常",
                    "quota": 1,
                },
            )

            updated = service.mark_image_result("token-1", success=True)

            self.assertIsNotNone(updated)
            self.assertEqual(updated["quota"], 0)
            self.assertEqual(updated["status"], "限流")

    def test_split_image_model_supports_plan_type_prefix(self) -> None:
        self.assertEqual(split_image_model("gpt-image-2"), (None, "gpt-image-2"))
        self.assertEqual(split_image_model("plus-codex-gpt-image-2"), ("plus", "codex-gpt-image-2"))
        self.assertEqual(split_image_model("team-codex-gpt-image-2"), ("team", "codex-gpt-image-2"))
        self.assertEqual(split_image_model("pro-codex-gpt-image-2"), ("pro", "codex-gpt-image-2"))
        self.assertEqual(split_image_model("plus-gpt-image-2"), (None, None))
        self.assertEqual(split_image_model("unknown-image-model"), (None, None))

    def test_get_available_access_token_filters_by_plan_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = AccountService(JSONStorageBackend(Path(tmp_dir) / "accounts.json"))
            service.add_account_items(
                [
                    {"access_token": "token-plus", "type": "Plus", "status": "正常", "quota": 3},
                    {"access_token": "token-pro", "type": "Pro", "status": "正常", "quota": 3},
                ]
            )

            service.fetch_remote_info = lambda access_token, event="fetch_remote_info": service.get_account(access_token)

            plus_token = service.get_available_access_token(plan_type="plus")
            pro_token = service.get_available_access_token(plan_type="pro")
            service.release_image_slot(plus_token)
            service.release_image_slot(pro_token)

            self.assertEqual(plus_token, "token-plus")
            self.assertEqual(pro_token, "token-pro")

    def test_refresh_accounts_can_remove_invalid_token_without_confirmation_delay(self) -> None:
        original_value = config.data.get("auto_remove_invalid_accounts")
        config.data["auto_remove_invalid_accounts"] = True
        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                service = AccountService(JSONStorageBackend(Path(tmp_dir) / "accounts.json"))
                service.add_account_items([{"access_token": "invalid-token", "status": "正常"}])

                with patch(
                    "services.openai_backend_api.OpenAIBackendAPI.get_user_info",
                    side_effect=InvalidAccessTokenError("token invalidated (/backend-api/me)"),
                ):
                    result = service.refresh_accounts(["invalid-token"], defer_invalid_removal=False)

                self.assertEqual(result["refreshed"], 0)
                self.assertEqual(len(result["errors"]), 1)
                self.assertEqual(result["items"], [])
                self.assertIsNone(service.get_account("invalid-token"))
        finally:
            if original_value is None:
                config.data.pop("auto_remove_invalid_accounts", None)
            else:
                config.data["auto_remove_invalid_accounts"] = original_value

    def test_refresh_accounts_defers_invalid_token_removal_by_default(self) -> None:
        original_value = config.data.get("auto_remove_invalid_accounts")
        config.data["auto_remove_invalid_accounts"] = True
        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                service = AccountService(JSONStorageBackend(Path(tmp_dir) / "accounts.json"))
                service.add_account_items([{"access_token": "invalid-token", "status": "正常"}])

                with patch(
                    "services.openai_backend_api.OpenAIBackendAPI.get_user_info",
                    side_effect=InvalidAccessTokenError("token invalidated (/backend-api/me)"),
                ):
                    result = service.refresh_accounts(["invalid-token"])

                account = service.get_account("invalid-token")
                self.assertEqual(result["refreshed"], 0)
                self.assertEqual(len(result["errors"]), 1)
                self.assertIsNotNone(account)
                self.assertEqual(account["invalid_count"], 1)
        finally:
            if original_value is None:
                config.data.pop("auto_remove_invalid_accounts", None)
            else:
                config.data["auto_remove_invalid_accounts"] = original_value


class TokenLogTests(unittest.TestCase):
    def test_anonymize_token_hides_raw_value(self) -> None:
        token = "super-secret-token"
        token_ref = anonymize_token(token)

        self.assertTrue(token_ref.startswith("token:"))
        self.assertNotIn(token, token_ref)


class AuthServiceTests(unittest.TestCase):
    def test_create_authenticate_disable_and_delete_user_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = AuthService(JSONStorageBackend(Path(tmp_dir) / "accounts.json", Path(tmp_dir) / "auth_keys.json"))

            item, raw_key = service.create_key(role="user", name="Alice")

            self.assertEqual(item["role"], "user")
            self.assertEqual(item["name"], "Alice")
            self.assertTrue(item["enabled"])
            self.assertTrue(raw_key.startswith("sk-"))

            authed = service.authenticate(raw_key)
            self.assertIsNotNone(authed)
            self.assertEqual(authed["id"], item["id"])
            self.assertEqual(authed["role"], "user")
            self.assertIsNotNone(authed["last_used_at"])

            updated = service.update_key(item["id"], {"enabled": False}, role="user")
            self.assertIsNotNone(updated)
            self.assertFalse(updated["enabled"])
            self.assertIsNone(service.authenticate(raw_key))

            self.assertTrue(service.delete_key(item["id"], role="user"))
            self.assertFalse(service.delete_key(item["id"], role="user"))
            self.assertEqual(service.list_keys(role="user"), [])

    def test_authenticate_ignores_last_used_save_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = AuthService(JSONStorageBackend(Path(tmp_dir) / "accounts.json", Path(tmp_dir) / "auth_keys.json"))
            item, raw_key = service.create_key(role="user", name="Alice")

            def fail_save() -> None:
                raise OSError("disk unavailable")

            service._save = fail_save

            authed = service.authenticate(raw_key)

            self.assertIsNotNone(authed)
            self.assertEqual(authed["id"], item["id"])
            self.assertIsNotNone(authed["last_used_at"])

    def test_update_user_key_replaces_raw_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = AuthService(JSONStorageBackend(Path(tmp_dir) / "accounts.json", Path(tmp_dir) / "auth_keys.json"))
            item, raw_key = service.create_key(role="user", name="Alice")

            updated = service.update_key(item["id"], {"key": "sk-user-custom-key"}, role="user")

            self.assertIsNotNone(updated)
            self.assertIsNone(service.authenticate(raw_key))

            authed = service.authenticate("sk-user-custom-key")
            self.assertIsNotNone(authed)
            self.assertEqual(authed["id"], item["id"])

    def test_user_key_name_must_be_unique(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = AuthService(JSONStorageBackend(Path(tmp_dir) / "accounts.json", Path(tmp_dir) / "auth_keys.json"))
            first, _ = service.create_key(role="user", name="Alice")
            second, _ = service.create_key(role="user", name="Bob")

            with self.assertRaisesRegex(ValueError, "这个名称已经在使用中了"):
                service.create_key(role="user", name="Alice")

            with self.assertRaisesRegex(ValueError, "这个名称已经在使用中了"):
                service.update_key(second["id"], {"name": "Alice"}, role="user")

            updated = service.update_key(first["id"], {"name": "Alice"}, role="user")
            self.assertIsNotNone(updated)
            self.assertEqual(updated["name"], "Alice")


if __name__ == "__main__":
    unittest.main()
