from __future__ import annotations

import json
import unittest
from unittest import mock

import requests

from services.protocol import openai_v1_models


AUTH_KEY = "chatgpt2api"
BASE_URL = "http://localhost:8000"


class ModelListTests(unittest.TestCase):
    def test_list_models_exposes_only_individually_allowed_codex_text_model(self):
        with (
            mock.patch.object(
                openai_v1_models.OpenAIBackendAPI,
                "list_models",
                return_value={"object": "list", "data": []},
            ),
            mock.patch.object(
                openai_v1_models.account_service,
                "list_accounts",
                return_value=[{
                    "access_token": "token-codex",
                    "source_type": "codex",
                    "status": "正常",
                    "allowed_models": ["gpt-5.6-terra"],
                }],
            ),
        ):
            result = openai_v1_models.list_models()

        ids = {item["id"] for item in result["data"]}
        codex_text_ids = {
            model
            for model in ids
            if model == "gpt-5.5" or model.startswith("gpt-5.6")
        }
        self.assertEqual(codex_text_ids, {"gpt-5.6-terra"})

    def test_list_models_exposes_all_codex_text_models_for_unrestricted_account(self):
        with (
            mock.patch.object(
                openai_v1_models.OpenAIBackendAPI,
                "list_models",
                return_value={"object": "list", "data": []},
            ),
            mock.patch.object(
                openai_v1_models.account_service,
                "list_accounts",
                return_value=[{
                    "access_token": "token-codex",
                    "source_type": "codex",
                    "status": "正常",
                }],
            ),
        ):
            result = openai_v1_models.list_models()

        ids = {item["id"] for item in result["data"]}
        codex_text_ids = {
            model
            for model in ids
            if model == "gpt-5.5" or model.startswith("gpt-5.6")
        }
        expected = {"gpt-5.5", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.6-sol"}
        self.assertEqual(codex_text_ids, expected)

    def test_list_models_exposes_codex_text_model_for_eligible_codex_account(self):
        with (
            mock.patch.object(
                openai_v1_models.OpenAIBackendAPI,
                "list_models",
                return_value={
                    "object": "list",
                    "data": [{"id": "gpt-5-5", "object": "model"}],
                },
            ),
            mock.patch.object(
                openai_v1_models.account_service,
                "list_accounts",
                return_value=[{
                    "access_token": "token-codex",
                    "source_type": "codex",
                    "status": "正常",
                    "allowed_models": ["gpt-5.5"],
                }],
            ),
        ):
            result = openai_v1_models.list_models()

        ids = [item["id"] for item in result["data"]]
        self.assertIn("gpt-5.5", ids)
        self.assertIn("gpt-5-5", ids)

    def test_list_models_hides_codex_text_model_without_eligible_codex_account(self):
        with (
            mock.patch.object(
                openai_v1_models.OpenAIBackendAPI,
                "list_models",
                return_value={
                    "object": "list",
                    "data": [{"id": "gpt-5-5", "object": "model"}],
                },
            ),
            mock.patch.object(
                openai_v1_models.account_service,
                "list_accounts",
                return_value=[
                    {
                        "access_token": "token-web",
                        "source_type": "web",
                        "status": "正常",
                        "allowed_models": ["gpt-5.5"],
                    },
                    {
                        "access_token": "token-disabled-codex",
                        "source_type": "codex",
                        "status": "禁用",
                        "allowed_models": ["gpt-5.5"],
                    },
                    {
                        "access_token": "token-limited-codex",
                        "source_type": "codex",
                        "status": "限流",
                        "allowed_models": ["gpt-5.5"],
                    },
                    {
                        "access_token": "token-other-model",
                        "source_type": "codex",
                        "status": "正常",
                        "allowed_models": ["gpt-5-5"],
                    },
                ],
            ),
        ):
            result = openai_v1_models.list_models()

        ids = [item["id"] for item in result["data"]]
        self.assertNotIn("gpt-5.5", ids)
        self.assertIn("gpt-5-5", ids)

    def test_list_models_only_returns_image_models_backed_by_account_types(self):
        with (
            mock.patch.object(
                openai_v1_models.OpenAIBackendAPI,
                "list_models",
                return_value={"object": "list", "data": []},
            ),
            mock.patch.object(
                openai_v1_models.account_service,
                "list_accounts",
                return_value=[
                    {"access_token": "token-free", "type": "free"},
                    {"access_token": "token-web-team", "type": "Team", "source_type": "web"},
                    {"access_token": "token-codex-team", "type": "Team", "source_type": "codex"},
                ],
            ),
        ):
            result = openai_v1_models.list_models()

        ids = {item["id"] for item in result["data"]}
        self.assertIn("gpt-image-2", ids)
        self.assertIn("codex-gpt-image-2", ids)
        self.assertIn("team-codex-gpt-image-2", ids)
        self.assertNotIn("plus-codex-gpt-image-2", ids)
        self.assertNotIn("pro-codex-gpt-image-2", ids)

    def test_list_models_does_not_return_codex_models_for_web_plus_accounts(self):
        with (
            mock.patch.object(
                openai_v1_models.OpenAIBackendAPI,
                "list_models",
                return_value={"object": "list", "data": []},
            ),
            mock.patch.object(
                openai_v1_models.account_service,
                "list_accounts",
                return_value=[
                    {"access_token": "token-web-plus", "type": "Plus", "source_type": "web"},
                ],
            ),
        ):
            result = openai_v1_models.list_models()

        ids = {item["id"] for item in result["data"]}
        self.assertIn("gpt-image-2", ids)
        self.assertNotIn("codex-gpt-image-2", ids)
        self.assertNotIn("plus-codex-gpt-image-2", ids)

    def test_list_models_function(self):
        """测试直接调用服务层获取模型列表。"""
        result = openai_v1_models.list_models()
        print("function result:")
        print(json.dumps(result, ensure_ascii=False, indent=2))

    def test_list_models_http(self):
        """测试通过 HTTP 接口获取模型列表。"""
        response = requests.get(
            f"{BASE_URL}/v1/models",
            headers={"Authorization": f"Bearer {AUTH_KEY}"},
            timeout=30,
        )
        print("http status:")
        print(response.status_code)
        print("http result:")
        print(json.dumps(response.json(), ensure_ascii=False, indent=2))
