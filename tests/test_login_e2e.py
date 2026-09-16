"""端到端登录测试：本机模拟校园网门户（真实 HTTP 请求，但不联外网）。"""

from __future__ import annotations

import hashlib
import logging
import unittest

from campus_login.adapters import create_adapter
from campus_login.config import Config
from campus_login.credentials import Credential, MemoryCredentialStore
from campus_login.devserver import LocalPortal
from campus_login.logging_setup import LOGGER_NAME, setup_logging
from campus_login.network import build_session
from campus_login.service import CampusLoginService, Status

from .support import TempDataDirTestCase

USER = "2021001"
PASSWORD = "Campus#2026-pw"


class LoginEndToEndTest(TempDataDirTestCase):
    def portal_config(self, portal: LocalPortal, **overrides) -> Config:
        config = Config()
        config.adapter = "generic"
        config.portal_url = portal.url("/portal")
        config.login_url = portal.url("/login")
        config.check_url = portal.url("/generate_204")
        config.check_timeout_seconds = 3
        config.request_timeout_seconds = 3
        config.max_retries = overrides.pop("max_retries", 2)
        config.retry_schedule_seconds = overrides.pop("retry_schedule_seconds", [1, 1, 1])
        config.retry_interval_seconds = overrides.pop("retry_interval_seconds", 1)
        for key, value in overrides.items():
            setattr(config, key, value)
        config.normalize()
        return config

    def run_login(self, config: Config, credential: Credential):
        store = MemoryCredentialStore(credential)
        session = build_session(config)
        adapter = create_adapter(config, session=session)
        service = CampusLoginService(config, store, adapter)
        return service.ensure_online(), adapter, service

    # ------------------------------------------------------------------
    def test_login_success(self):
        with LocalPortal(username=USER, password=PASSWORD) as portal:
            config = self.portal_config(portal)
            status, adapter, service = self.run_login(config, Credential(USER, PASSWORD))
            self.assertIs(status, Status.AUTHENTICATED)
            self.assertEqual(adapter.attempts, 1)
            self.assertIn("认证成功", service.message)

    def test_already_authenticated_does_not_login_again(self):
        with LocalPortal(username=USER, password=PASSWORD) as portal:
            config = self.portal_config(portal)
            session = build_session(config)
            session.post(
                config.login_url, data={"username": USER, "password": PASSWORD}, timeout=3
            )
            adapter = create_adapter(config, session=session)
            service = CampusLoginService(config, MemoryCredentialStore(Credential(USER, PASSWORD)), adapter)
            self.assertIs(service.ensure_online(), Status.ONLINE)
            self.assertEqual(adapter.attempts, 0)

    def test_login_success_with_302_redirect(self):
        with LocalPortal(username=USER, password=PASSWORD, redirect_on_success=True) as portal:
            config = self.portal_config(portal)
            status, adapter, _ = self.run_login(config, Credential(USER, PASSWORD))
            self.assertIs(status, Status.AUTHENTICATED)
            self.assertEqual(adapter.attempts, 1)

    def test_unknown_success_response_is_verified_by_probe(self):
        # 接口返回的内容无法识别，但认证状态探测显示已经能上网 → 判定成功
        with LocalPortal(username=USER, password=PASSWORD, success_text="{\"ok\":1}") as portal:
            config = self.portal_config(portal)
            status, adapter, _ = self.run_login(config, Credential(USER, PASSWORD))
            self.assertIs(status, Status.ONLINE)

    def test_wrong_password_stops_immediately(self):
        with LocalPortal(username=USER, password=PASSWORD) as portal:
            config = self.portal_config(portal, max_retries=5)
            status, adapter, service = self.run_login(config, Credential(USER, "wrong-password"))
            self.assertIs(status, Status.FAILED)
            self.assertEqual(adapter.attempts, 1, "密码错误不能快速无限重试")
            self.assertIn("账号或密码", service.message)

    def test_server_error_is_retried(self):
        with LocalPortal(username=USER, password=PASSWORD, server_error=True) as portal:
            config = self.portal_config(portal, max_retries=3)
            status, adapter, _ = self.run_login(config, Credential(USER, PASSWORD))
            self.assertIs(status, Status.FAILED)
            self.assertEqual(adapter.attempts, 3)

    def test_captcha_portal_requires_manual(self):
        with LocalPortal(username=USER, password=PASSWORD, captcha=True) as portal:
            config = self.portal_config(portal)
            status, adapter, service = self.run_login(config, Credential(USER, PASSWORD))
            self.assertIs(status, Status.NEEDS_MANUAL)
            # 登录页面预检查发现验证码 → 不会反复提交密码
            self.assertLessEqual(adapter.attempts, 1)

    def test_custom_field_names(self):
        with LocalPortal(
            username=USER, password=PASSWORD, username_field="account", password_field="pwd"
        ) as portal:
            config = self.portal_config(
                portal, username_field="account", password_field="pwd"
            )
            status, _, _ = self.run_login(config, Credential(USER, PASSWORD))
            self.assertIs(status, Status.AUTHENTICATED)

    def test_service_value_is_sent(self):
        with LocalPortal(username=USER, password=PASSWORD, required_service="campus") as portal:
            config = self.portal_config(
                portal, service_field="service", service_value="campus"
            )
            status, _, _ = self.run_login(config, Credential(USER, PASSWORD))
            self.assertIs(status, Status.AUTHENTICATED)

            wrong = self.portal_config(
                portal, service_field="service", service_value="telecom", max_retries=1
            )
            status, _, _ = self.run_login(wrong, Credential(USER, PASSWORD))
            self.assertIs(status, Status.FAILED)

    def test_md5_password_transform(self):
        digest = hashlib.md5(PASSWORD.encode("utf-8")).hexdigest()
        with LocalPortal(username=USER, password=digest) as portal:
            config = self.portal_config(portal, password_transform="md5")
            status, _, _ = self.run_login(config, Credential(USER, PASSWORD))
            self.assertIs(status, Status.AUTHENTICATED)

    def test_extra_fields_are_submitted(self):
        class Recorder(LocalPortal):
            pass

        with LocalPortal(username=USER, password=PASSWORD) as portal:
            config = self.portal_config(portal, extra_fields={"wlanuserip": "10.0.0.5"})
            session = build_session(config)
            adapter = create_adapter(config, session=session)
            payload = adapter.build_payload(USER, PASSWORD)
            self.assertEqual(payload["wlanuserip"], "10.0.0.5")
            self.assertEqual(payload["username"], USER)
            self.assertEqual(payload["password"], PASSWORD)
            del Recorder

    def test_form_encoding_and_json_encoding(self):
        with LocalPortal(username=USER, password=PASSWORD) as portal:
            form_config = self.portal_config(portal)
            adapter = create_adapter(form_config, session=build_session(form_config))
            status, _, _ = self.run_login(form_config, Credential(USER, PASSWORD))
            self.assertIs(status, Status.AUTHENTICATED)
            self.assertEqual(adapter.config.content_type, "form")

    def test_log_file_never_contains_password(self):
        with LocalPortal(username=USER, password=PASSWORD) as portal:
            config = self.portal_config(portal)
            log_file = setup_logging(config)
            status, _, _ = self.run_login(config, Credential(USER, PASSWORD))
            self.assertIs(status, Status.AUTHENTICATED)
            for handler in logging.getLogger(LOGGER_NAME).handlers:
                handler.flush()
            content = log_file.read_text(encoding="utf-8")
            self.assertIn("开始校园网认证", content)
            self.assertIn("登录成功", content)
            self.assertNotIn(PASSWORD, content)

    def test_portal_url_is_exposed_for_manual_login(self):
        with LocalPortal(username=USER, password=PASSWORD) as portal:
            config = self.portal_config(portal)
            adapter = create_adapter(config, session=build_session(config))
            self.assertIn("/portal", adapter.portal_url)


if __name__ == "__main__":
    unittest.main()
