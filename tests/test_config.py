"""配置读取 / 保存测试。"""

from __future__ import annotations

import json
import unittest

from campus_login.config import (
    DEFAULT_PROBE_URLS,
    Config,
    load_config,
    probe_urls,
    save_config,
)
from campus_login.paths import config_path

from .support import TempDataDirTestCase


class ConfigTest(TempDataDirTestCase):
    def test_defaults_when_no_file(self):
        config = load_config()
        self.assertEqual(config.adapter, "generic")
        self.assertEqual(config.request_method, "POST")
        self.assertEqual(config.max_retries, 5)
        self.assertEqual(config.retry_schedule_seconds, [5.0, 10.0, 20.0, 30.0, 60.0])
        self.assertFalse(config.auto_start_on_boot)
        self.assertTrue(config.captcha_keywords)

    def test_save_and_load_round_trip(self):
        config = Config()
        config.adapter = "gdufe"
        config.login_url = "http://10.0.0.1/login"
        config.check_url = "http://10.0.0.1/check"
        config.username = "2021001"
        config.service_value = "校园网"
        config.extra_fields = {"nasip": "10.0.0.1"}
        config.retry_schedule_seconds = [3, 6, 12]
        config.max_retries = 7
        path = save_config(config)
        self.assertTrue(path.exists())

        loaded = load_config()
        self.assertEqual(loaded.adapter, "gdufe")
        self.assertEqual(loaded.login_url, "http://10.0.0.1/login")
        self.assertEqual(loaded.username, "2021001")
        self.assertEqual(loaded.extra_fields, {"nasip": "10.0.0.1"})
        self.assertEqual(loaded.retry_schedule_seconds, [3.0, 6.0, 12.0])
        self.assertEqual(loaded.max_retries, 7)

    def test_config_file_never_contains_password(self):
        config = Config()
        config.username = "2021001"
        save_config(config)
        raw = config_path().read_text(encoding="utf-8")
        self.assertNotIn("password_value", raw)
        data = json.loads(raw)
        self.assertNotIn("password", data)

    def test_unknown_fields_are_ignored(self):
        path = config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"adapter": "generic", "某学校专用字段": 1, "max_retries": 2}),
            encoding="utf-8",
        )
        config = load_config()
        self.assertEqual(config.max_retries, 2)
        self.assertFalse(hasattr(config, "某学校专用字段"))

    def test_invalid_values_are_normalised(self):
        config = Config.from_dict(
            {
                "adapter": "unknown-school",
                "request_method": "put",
                "content_type": "xml",
                "password_transform": "rot13",
                "max_retries": -5,
                "check_timeout_seconds": 9999,
                "retry_schedule_seconds": "oops",
                "captcha_keywords": "验证码\n短信验证",
                "auto_start_on_boot": True,
            }
        )
        self.assertEqual(config.adapter, "generic")
        self.assertEqual(config.request_method, "POST")
        self.assertEqual(config.content_type, "form")
        self.assertEqual(config.password_transform, "none")
        self.assertEqual(config.max_retries, 1)
        self.assertLessEqual(config.check_timeout_seconds, 120)
        self.assertEqual(config.retry_schedule_seconds, [5.0, 10.0, 20.0, 30.0, 60.0])
        self.assertEqual(config.captcha_keywords, ["验证码", "短信验证"])
        self.assertTrue(config.auto_start_on_boot)

    def test_broken_config_is_backed_up(self):
        path = config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{ this is not json", encoding="utf-8")
        config = load_config()
        self.assertEqual(config.adapter, "generic")
        self.assertTrue(path.with_suffix(path.suffix + ".bad").exists())

    def test_probe_urls(self):
        config = Config()
        self.assertEqual(probe_urls(config), DEFAULT_PROBE_URLS)
        config.check_url = "http://10.0.0.1/generate_204"
        self.assertEqual(probe_urls(config), ["http://10.0.0.1/generate_204"])

    def test_extra_fields_are_loaded_from_strings(self):
        config = Config.from_dict({"extra_fields": {"a": 1, "b": None}})
        self.assertEqual(config.extra_fields, {"a": "1", "b": "None"})

    def test_config_with_utf8_bom_is_accepted(self):
        # 记事本 / PowerShell 写出的 JSON 可能带 BOM，必须能正常读取
        path = config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"max_retries": 4}), encoding="utf-8-sig")
        config = load_config()
        self.assertEqual(config.max_retries, 4)


if __name__ == "__main__":
    unittest.main()
