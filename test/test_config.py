import json
import tempfile
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
ROOT_CONFIG_FILE = ROOT_DIR / "config.json"


class ConfigLoadingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._created_root_config = False
        if not ROOT_CONFIG_FILE.exists():
            ROOT_CONFIG_FILE.write_text(json.dumps({"auth-key": "test-auth"}), encoding="utf-8")
            cls._created_root_config = True

        from services import config as config_module

        cls.config_module = config_module

    @classmethod
    def tearDownClass(cls) -> None:
        if cls._created_root_config and ROOT_CONFIG_FILE.exists():
            ROOT_CONFIG_FILE.unlink()

    def test_load_settings_ignores_directory_config_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base_dir = Path(tmp_dir)
            data_dir = base_dir / "data"
            config_dir = base_dir / "config.json"
            os_auth_key = "env-auth"

            config_dir.mkdir()

            module = self.config_module
            old_base_dir = module.BASE_DIR
            old_data_dir = module.DATA_DIR
            old_config_file = module.CONFIG_FILE
            old_env_auth_key = module.os.environ.get("CHATGPT2API_AUTH_KEY")
            try:
                module.BASE_DIR = base_dir
                module.DATA_DIR = data_dir
                module.CONFIG_FILE = config_dir
                module.os.environ["CHATGPT2API_AUTH_KEY"] = os_auth_key

                settings = module._load_settings()

                self.assertEqual(settings.auth_key, os_auth_key)
                self.assertEqual(settings.refresh_account_interval_minute, 5)
            finally:
                module.BASE_DIR = old_base_dir
                module.DATA_DIR = old_data_dir
                module.CONFIG_FILE = old_config_file
                if old_env_auth_key is None:
                    module.os.environ.pop("CHATGPT2API_AUTH_KEY", None)
                else:
                    module.os.environ["CHATGPT2API_AUTH_KEY"] = old_env_auth_key

    def test_image_thinking_effort_is_normalized_and_exposed(self) -> None:
        module = self.config_module
        missing = object()
        cases = (
            (missing, "high"),
            ("", ""),
            ("low", "low"),
            ("medium", "medium"),
            ("high", "high"),
            ("extended", "extended"),
            (" HIGH ", "high"),
            ("unexpected", "high"),
        )

        for raw_value, expected in cases:
            with self.subTest(raw_value=raw_value), tempfile.TemporaryDirectory() as tmp_dir:
                config_path = Path(tmp_dir) / "config.json"
                payload: dict[str, object] = {"auth-key": "test-auth"}
                if raw_value is not missing:
                    payload["image_thinking_effort"] = raw_value
                config_path.write_text(json.dumps(payload), encoding="utf-8")

                store = module.ConfigStore(config_path)

                self.assertEqual(store.image_thinking_effort, expected)
                self.assertEqual(store.get()["image_thinking_effort"], expected)

    def test_image_thinking_effort_update_is_persisted(self) -> None:
        module = self.config_module
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "config.json"
            config_path.write_text(json.dumps({"auth-key": "test-auth"}), encoding="utf-8")
            store = module.ConfigStore(config_path)

            updated = store.update({"image_thinking_effort": "medium"})

            persisted = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(updated["image_thinking_effort"], "medium")
            self.assertEqual(persisted["image_thinking_effort"], "medium")

    def test_codex_image_quality_is_normalized_and_exposed(self) -> None:
        module = self.config_module
        missing = object()
        cases = (
            (missing, "auto"),
            ("auto", "auto"),
            ("low", "low"),
            ("medium", "medium"),
            ("high", "high"),
            (" HIGH ", "high"),
            ("unexpected", "auto"),
        )

        for raw_value, expected in cases:
            with self.subTest(raw_value=raw_value), tempfile.TemporaryDirectory() as tmp_dir:
                config_path = Path(tmp_dir) / "config.json"
                payload: dict[str, object] = {"auth-key": "test-auth"}
                if raw_value is not missing:
                    payload["codex_image_quality"] = raw_value
                config_path.write_text(json.dumps(payload), encoding="utf-8")

                store = module.ConfigStore(config_path)

                self.assertEqual(store.codex_image_quality, expected)
                self.assertEqual(store.get()["codex_image_quality"], expected)

    def test_codex_image_quality_update_is_normalized_and_persisted(self) -> None:
        module = self.config_module
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "config.json"
            config_path.write_text(json.dumps({"auth-key": "test-auth"}), encoding="utf-8")
            store = module.ConfigStore(config_path)

            updated = store.update({"codex_image_quality": " HIGH "})

            persisted = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(updated["codex_image_quality"], "high")
            self.assertEqual(persisted["codex_image_quality"], "high")


if __name__ == "__main__":
    unittest.main()
