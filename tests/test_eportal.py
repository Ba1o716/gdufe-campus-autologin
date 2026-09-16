"""ePortal（深澜 / 城市热点）JSONP 接口解析与判定测试。

对应真实抓包结果：
    GET http://100.64.13.17:801/eportal/portal/login
    dr1003({"result":1,"msg":"Portal协议认证成功！"});
"""

from __future__ import annotations

import unittest

from campus_login.adapters import create_adapter
from campus_login.adapters.base import BaseAdapter, extract_jsonp
from campus_login.adapters.generic_portal import detect_captcha_field
from campus_login.config import Config
from campus_login.credentials import Credential, MemoryCredentialStore
from campus_login.devserver import LocalPortal
from campus_login.network import build_session
from campus_login.service import CampusLoginService, Status

from .support import TempDataDirTestCase

SUCCESS_JSONP = 'dr1003({"result":1,"msg":"Portal协议认证成功！"});'
USER = "20250101001"
PASSWORD = "test-password"


class ExtractJsonpTest(unittest.TestCase):
    def test_jsonp_wrapped_object(self):
        data, wrapped = extract_jsonp(SUCCESS_JSONP)
        self.assertTrue(wrapped)
        self.assertEqual(data, {"result": 1, "msg": "Portal协议认证成功！"})

    def test_bare_json(self):
        data, wrapped = extract_jsonp('{"result":1,"msg":"ok"}')
        self.assertFalse(wrapped)
        self.assertEqual(data["result"], 1)

    def test_jsonp_with_and_expression(self):
        data, wrapped = extract_jsonp('jsonpCallback && jsonpCallback({"result":0,"msg":"错误"});')
        self.assertTrue(wrapped)
        self.assertEqual(data["result"], 0)

    def test_bom_and_whitespace(self):
        data, _ = extract_jsonp('\ufeff  dr1003({"result":1}) \r\n ')
        self.assertEqual(data, {"result": 1})

    def test_html_is_not_json(self):
        data, wrapped = extract_jsonp("<html><body>请登录校园网</body></html>")
        self.assertIsNone(data)
        self.assertFalse(wrapped)

    def test_empty_text(self):
        self.assertEqual(extract_jsonp(""), (None, False))


class EPortalClassificationTest(TempDataDirTestCase):
    def build_adapter(self, adapter_name: str = "eportal", **overrides):
        config = Config()
        config.adapter = adapter_name
        for key, value in overrides.items():
            setattr(config, key, value)
        config.normalize()
        adapter = create_adapter(config, session=build_session(config))
        adapter.remember(Credential(USER, PASSWORD))
        return adapter, config

    def test_result_one_is_success(self):
        adapter, _ = self.build_adapter()
        status, message = adapter.classify(200, SUCCESS_JSONP)
        self.assertIs(status.name, "SUCCESS")
        self.assertIn("Portal 认证成功", message)
        self.assertIn("Portal协议认证成功", message)

    def test_result_one_with_empty_msg(self):
        adapter, _ = self.build_adapter()
        status, _ = adapter.classify(200, "dr1003({\"result\":1});")
        self.assertEqual(status.name, "SUCCESS")

    def test_wrong_password_message(self):
        adapter, _ = self.build_adapter()
        status, message = adapter.classify(200, 'dr1003({"result":0,"msg":"用户名或密码错误"});')
        self.assertEqual(status.name, "INVALID_CREDENTIALS")
        self.assertIn("账号或密码", message)
        self.assertIn("用户名或密码错误", message)

    def test_captcha_message(self):
        adapter, _ = self.build_adapter()
        status, message = adapter.classify(200, 'dr1003({"result":0,"msg":"请输入验证码"});')
        self.assertEqual(status.name, "CAPTCHA_REQUIRED")
        self.assertIn("验证码", message)

    def test_already_online_message(self):
        adapter, _ = self.build_adapter()
        status, message = adapter.classify(200, 'dr1003({"result":0,"msg":"您已在线"});')
        self.assertEqual(status.name, "SUCCESS")
        self.assertIn("已在线", message)

    def test_other_failure_message_is_reported(self):
        adapter, _ = self.build_adapter()
        status, message = adapter.classify(200, 'dr1003({"result":0,"msg":"AC 拒绝接入"});')
        self.assertEqual(status.name, "FAILED")
        self.assertIn("AC 拒绝接入", message)

    def test_generic_adapter_also_parses_jsonp(self):
        # 关键：即使适配器仍是 generic（用户当前配置），也能正确解析 ePortal 的 JSONP
        adapter, _ = self.build_adapter("generic")
        status, message = adapter.classify(200, SUCCESS_JSONP)
        self.assertEqual(status.name, "SUCCESS")
        self.assertIn("Portal 认证成功", message)

    def test_eportal_adapter_defaults(self):
        adapter, config = self.build_adapter(
            "eportal", login_url="http://100.64.13.17:801/eportal/portal/login"
        )
        # 通用 ePortal 适配器使用 ePortal 的常见参数名作为类默认值
        self.assertEqual(type(adapter).USERNAME_FIELD, "user_account")
        self.assertEqual(type(adapter).PASSWORD_FIELD, "user_password")
        self.assertEqual(config.extra_fields.get("callback"), "dr1003")
        self.assertEqual(adapter.portal_url, "http://100.64.13.17:801/eportal/portal/login")

    def test_gdufe_adapter_fills_confirmed_values(self):
        config = Config()
        config.adapter = "gdufe"
        config.normalize()
        adapter = create_adapter(config, session=build_session(config))
        self.assertEqual(
            adapter.config.login_url, "http://100.64.13.17:801/eportal/portal/login"
        )
        self.assertEqual(adapter.config.username_field, "user_account")
        self.assertEqual(adapter.config.password_field, "user_password")

    def test_gdufe_builds_confirmed_request_parameters(self):
        """按 2026-09-16 抓包确认的参数构造请求。"""
        config = Config()
        config.adapter = "gdufe"
        config.normalize()
        adapter = create_adapter(config, session=build_session(config))
        payload = adapter.build_payload("20250101001", "pwd-123")

        self.assertEqual(payload["callback"], "dr1003")
        self.assertEqual(payload["login_method"], "1")
        # 抓包确认账号带 ",0," 前缀（URL 里是 %2C0%2C20250101001）
        self.assertEqual(payload["user_account"], ",0,20250101001")
        self.assertEqual(payload["user_password"], "pwd-123")
        self.assertEqual(payload["wlan_user_mac"], "000000000000")
        self.assertEqual(payload["wlan_ac_ip"], "100.64.13.18")
        self.assertEqual(payload["jsVersion"], "4.1.3")
        self.assertEqual(payload["terminal_type"], "1")
        self.assertEqual(payload["lang"], "zh-cn")
        self.assertTrue(payload["v"].isdigit(), payload["v"])
        self.assertEqual(payload["wlan_user_ipv6"], "")
        self.assertEqual(payload["wlan_ac_name"], "")
        # wlan_user_ip 自动取校园网网卡地址（不能是 Clash 的 198.18.x.x）
        self.assertTrue(payload["wlan_user_ip"], "wlan_user_ip 不应为空")
        self.assertFalse(payload["wlan_user_ip"].startswith("198.18."))
        self.assertFalse(payload["wlan_user_ip"].startswith("127."))

    def test_prepared_url_matches_capture(self):
        """实际发出的查询串要和抓包一致（含 %2C0%2C 前缀、明文密码）。"""
        import requests

        config = Config()
        config.adapter = "gdufe"
        config.normalize()
        adapter = create_adapter(config, session=build_session(config))
        payload = adapter.build_payload("20250101001", "test-password")
        prepared = requests.Request(
            "GET", adapter.config.login_url, params=payload
        ).prepare()
        url = prepared.url
        self.assertIn("user_account=%2C0%2C20250101001", url)
        self.assertIn("user_password=test-password", url)   # 明文提交（抓包确认不是 MD5）
        self.assertIn("callback=dr1003", url)
        self.assertIn("login_method=1", url)
        self.assertIn("wlan_user_mac=000000000000", url)
        self.assertIn("wlan_ac_ip=100.64.13.18", url)
        self.assertIn("jsVersion=4.1.3", url)
        self.assertIn("terminal_type=1", url)

    def test_password_transform_none_is_default(self):
        config = Config()
        config.adapter = "gdufe"
        config.normalize()
        self.assertEqual(config.password_transform, "none")
        adapter = create_adapter(config, session=build_session(config))
        self.assertEqual(
            adapter.build_payload("20250101001", "test-password")["user_password"], "test-password"
        )

    def test_username_prefix_can_be_overridden(self):
        config = Config()
        config.adapter = "gdufe"
        config.username_prefix = "自定义-"
        config.normalize()
        adapter = create_adapter(config, session=build_session(config))
        self.assertEqual(
            adapter.build_payload("20250101001", "x")["user_account"], "自定义-20250101001"
        )

    def test_generic_adapter_has_no_prefix_by_default(self):
        config = Config()
        config.adapter = "generic"
        config.login_url = "http://10.0.0.1/login"
        config.normalize()
        adapter = create_adapter(config, session=build_session(config))
        self.assertEqual(adapter.build_payload("user1", "x")["username"], "user1")

    def test_wlan_ac_ip_follows_portal_url(self):
        """门户页面里的 wlanacip 会变成请求里的 wlan_ac_ip（不同 AC 的同学都能用）。"""
        config = Config()
        config.adapter = "gdufe"
        config.portal_url = "http://192.0.2.10/a79.htm?wlanacip=192.0.2.99"
        config.normalize()
        adapter = create_adapter(config, session=build_session(config))
        self.assertEqual(adapter.build_payload(USER, PASSWORD)["wlan_ac_ip"], "192.0.2.99")

    def test_wlan_ac_ip_falls_back_to_default(self):
        config = Config()
        config.adapter = "gdufe"
        config.normalize()
        adapter = create_adapter(config, session=build_session(config))
        self.assertEqual(adapter.build_payload(USER, PASSWORD)["wlan_ac_ip"], "100.64.13.18")

    def test_local_ip_placeholder_expansion(self):
        import campus_login.adapters.base as base_module

        original = base_module.local_ipv4_prefer_physical
        base_module.local_ipv4_prefer_physical = lambda host="": "10.20.30.40"
        try:
            config = Config()
            config.login_url = "http://100.64.13.17:801/eportal/portal/login"
            config.extra_fields = {"wlan_user_ip": "{local_ip}", "v": "{random}"}
            config.normalize()
            adapter = create_adapter(config, session=build_session(config))
            payload = adapter.build_payload(USER, PASSWORD)
            self.assertEqual(payload["wlan_user_ip"], "10.20.30.40")
            self.assertTrue(payload["v"].isdigit())
        finally:
            base_module.local_ipv4_prefer_physical = original

    def test_config_overrides_adapter_defaults(self):
        config = Config()
        config.adapter = "gdufe"
        config.login_url = "http://10.0.0.9/eportal/portal/login"
        config.username_field = "自定义字段"
        config.normalize()
        adapter = create_adapter(config, session=build_session(config))
        self.assertEqual(adapter.config.login_url, "http://10.0.0.9/eportal/portal/login")
        self.assertEqual(adapter.config.username_field, "自定义字段")

    def test_eportal_url_switches_method_to_get(self):
        # 配置里是默认的 POST，但 ePortal 登录接口必须是 GET
        config = Config()
        config.login_url = "http://100.64.13.17:801/eportal/portal/login"
        config.normalize()
        self.assertEqual(config.request_method, "POST")
        adapter = create_adapter(config, session=build_session(config))
        self.assertEqual(adapter.config.request_method, "GET")

    def test_non_eportal_url_keeps_method(self):
        config = Config()
        config.login_url = "http://10.0.0.1/login"
        config.normalize()
        adapter = create_adapter(config, session=build_session(config))
        self.assertEqual(adapter.config.request_method, "POST")


class CaptchaPrecheckTest(TempDataDirTestCase):
    """a79.htm 这类门户正文里出现“验证码”字样，不能误判为需要验证码。"""

    page_with_text_hint = (
        "<html><body><h2>请登录校园网</h2>"
        "<form action='/login' method='post'>"
        "<input type='text' name='user_account'><input type='password' name='user_password'>"
        "</form><p>忘记密码？可通过手机验证码找回，或咨询网络中心。</p></body></html>"
    )
    page_with_captcha_input = (
        "<html><body><form action='/login' method='post'>"
        "<input type='text' name='captcha' placeholder='验证码'>"
        "</form></body></html>"
    )

    def test_detect_captcha_field(self):
        keywords = Config().captcha_keywords
        self.assertIsNone(detect_captcha_field(self.page_with_text_hint, keywords))
        self.assertEqual(detect_captcha_field(self.page_with_captcha_input, keywords), "captcha")
        self.assertIsNotNone(
            detect_captcha_field("<img src='/captcha.png' alt='验证码'>", keywords)
        )

    def test_precheck_ignores_body_text_hint(self):
        class FakeResponse:
            status_code = 200
            text = self.page_with_text_hint

        class FakeSession:
            def get(self, *args, **kwargs):
                return FakeResponse()

        config = Config()
        config.portal_url = "http://100.64.13.17/a79.htm"
        adapter = create_adapter(config, session=FakeSession())
        self.assertIsNone(adapter._precheck_captcha())

    def test_precheck_flags_real_captcha_input(self):
        class FakeResponse:
            status_code = 200
            text = self.page_with_captcha_input

        class FakeSession:
            def get(self, *args, **kwargs):
                return FakeResponse()

        config = Config()
        config.portal_url = "http://100.64.13.17/a79.htm"
        adapter = create_adapter(config, session=FakeSession())
        message = adapter._precheck_captcha() or ""
        self.assertTrue(message, "应当识别出验证码输入框")
        self.assertIn("手动", message)

    def test_precheck_can_be_disabled(self):
        calls: list[str] = []

        class FakeSession:
            def get(self, url, *args, **kwargs):
                calls.append(str(url))
                raise RuntimeError("模拟网络不可用")

            def post(self, *args, **kwargs):
                raise RuntimeError("模拟网络不可用")

        config = Config()
        config.portal_url = "http://100.64.13.17/a79.htm"
        config.login_url = "http://100.64.13.17:801/eportal/portal/login"
        config.captcha_precheck = False
        adapter = create_adapter(config, session=FakeSession())
        result = adapter.login(Credential(USER, PASSWORD))
        # 关闭预检查后，不应再去 GET 门户页面 a79.htm（登录请求本身可以发）
        self.assertTrue(all("a79.htm" not in url for url in calls), calls)
        self.assertEqual(result.status.name, "NETWORK_ERROR")


class EPortalEndToEndTest(TempDataDirTestCase):
    """走真实 HTTP：ePortal JSONP 返回 result=1，但状态探测仍被重定向。"""

    def eportal_config(self, portal: LocalPortal, adapter: str = "eportal") -> Config:
        config = Config()
        config.adapter = adapter
        config.portal_url = portal.url("/portal")
        config.login_url = portal.url("/eportal/portal/login")
        config.check_url = portal.url("/generate_204")
        config.request_method = "GET"
        config.username_field = "username"
        config.password_field = "password"
        config.check_timeout_seconds = 3
        config.request_timeout_seconds = 3
        config.normalize()
        return config

    def run_login(self, config: Config, credential: Credential):
        store = MemoryCredentialStore(credential)
        adapter = create_adapter(config, session=build_session(config))
        service = CampusLoginService(config, store, adapter)
        return service.ensure_online(), adapter, service

    def test_result_one_wins_even_when_probe_keeps_redirecting(self):
        with LocalPortal(
            username=USER,
            password=PASSWORD,
            eportal=True,
            probe_still_hijacked=True,
        ) as portal:
            config = self.eportal_config(portal)
            status, adapter, service = self.run_login(config, Credential(USER, PASSWORD))
            self.assertIs(status, Status.AUTHENTICATED)
            self.assertEqual(adapter.attempts, 1)
            self.assertNotIn("重定向", service.message)

    def test_generic_adapter_handles_eportal_interface(self):
        with LocalPortal(
            username=USER, password=PASSWORD, eportal=True, probe_still_hijacked=True
        ) as portal:
            config = self.eportal_config(portal, adapter="generic")
            status, _, _ = self.run_login(config, Credential(USER, PASSWORD))
            self.assertIs(status, Status.AUTHENTICATED)

    def test_internet_check_separates_two_states(self):
        with LocalPortal(
            username=USER, password=PASSWORD, eportal=True, probe_still_hijacked=True
        ) as portal:
            config = self.eportal_config(portal)
            config.internet_check_url = portal.url("/generate_204")
            status, _, service = self.run_login(config, Credential(USER, PASSWORD))
            self.assertIs(status, Status.AUTHENTICATED)
            self.assertFalse(service.check_internet())

        with LocalPortal(username=USER, password=PASSWORD, eportal=True) as portal:
            config = self.eportal_config(portal)
            config.internet_check_url = portal.url("/generate_204")
            status, _, _ = self.run_login(config, Credential(USER, PASSWORD))
            self.assertIs(status, Status.ONLINE)

    def test_wrong_password_reports_msg(self):
        with LocalPortal(username=USER, password=PASSWORD, eportal=True) as portal:
            config = self.eportal_config(portal)
            config.max_retries = 3
            status, adapter, service = self.run_login(config, Credential(USER, "bad-password"))
            self.assertIs(status, Status.FAILED)
            self.assertEqual(adapter.attempts, 1)
            self.assertIn("密码", service.message)


if __name__ == "__main__":
    unittest.main()
