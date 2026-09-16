"""网络检测 / 认证状态检测测试（使用本机模拟门户，不访问真实校园网）。"""

from __future__ import annotations

import unittest

from campus_login.config import Config
from campus_login.devserver import LocalPortal
from campus_login.network import (
    AuthState,
    all_local_ipv4,
    build_session,
    check_authentication,
    contains_any,
    is_virtual_ipv4,
    local_ipv4,
    local_ipv4_prefer_physical,
    looks_like_portal,
    network_ready,
    probe,
    wait_for_local_network,
)
from campus_login.retry import FakeSleeper

from .support import TempDataDirTestCase


class LocalNetworkTest(TempDataDirTestCase):
    def test_virtual_network_detection(self):
        # Clash / Surge 等 TUN 代理使用的 fake-ip 网段
        self.assertTrue(is_virtual_ipv4("198.18.0.1"))
        self.assertTrue(is_virtual_ipv4("198.19.10.5"))
        self.assertFalse(is_virtual_ipv4("198.20.0.1"))
        self.assertFalse(is_virtual_ipv4("10.20.30.40"))
        self.assertFalse(is_virtual_ipv4("192.168.1.10"))
        self.assertFalse(is_virtual_ipv4("100.64.13.17"))

    def test_prefer_physical_skips_virtual_adapter(self):
        import campus_login.winiface as winiface_module
        import campus_login.network as network_module

        original_all = network_module.all_local_ipv4
        original_source = network_module._source_ipv4_towards
        original_physical = winiface_module.physical_ipv4_addresses
        network_module.all_local_ipv4 = lambda: ["198.18.0.1", "10.20.30.40"]
        network_module._source_ipv4_towards = lambda host, port=80: "198.18.0.1"
        winiface_module.physical_ipv4_addresses = lambda: []
        try:
            # 只有虚拟网卡在默认路由上时，应当从全部地址里挑出真实网卡地址
            self.assertEqual(network_module.local_ipv4_prefer_physical("100.64.13.17"), "10.20.30.40")
        finally:
            network_module.all_local_ipv4 = original_all
            network_module._source_ipv4_towards = original_source
            winiface_module.physical_ipv4_addresses = original_physical

    def test_prefer_physical_uses_real_adapter_first(self):
        import campus_login.winiface as winiface_module
        import campus_login.network as network_module

        original_physical = winiface_module.physical_ipv4_addresses
        original_source = network_module._source_ipv4_towards
        winiface_module.physical_ipv4_addresses = lambda: ["10.20.30.40"]
        network_module._source_ipv4_towards = lambda host, port=80: "198.18.0.1"
        try:
            self.assertEqual(network_module.local_ipv4_prefer_physical(), "10.20.30.40")
        finally:
            winiface_module.physical_ipv4_addresses = original_physical
            network_module._source_ipv4_towards = original_source

    def test_prefer_physical_falls_back_when_only_virtual(self):
        import campus_login.winiface as winiface_module
        import campus_login.network as network_module

        original_all = network_module.all_local_ipv4
        original_source = network_module._source_ipv4_towards
        original_physical = winiface_module.physical_ipv4_addresses
        network_module.all_local_ipv4 = lambda: []
        network_module._source_ipv4_towards = lambda host, port=80: "198.18.0.1"
        winiface_module.physical_ipv4_addresses = lambda: []
        try:
            self.assertEqual(network_module.local_ipv4_prefer_physical(), "198.18.0.1")
        finally:
            network_module.all_local_ipv4 = original_all
            network_module._source_ipv4_towards = original_source
            winiface_module.physical_ipv4_addresses = original_physical

    def test_prefer_physical_in_real_environment(self):
        candidates = all_local_ipv4()
        picked = local_ipv4_prefer_physical()
        if any(not is_virtual_ipv4(address) for address in candidates):
            self.assertIsNotNone(picked)
            self.assertFalse(is_virtual_ipv4(picked))

    def test_local_ipv4_does_not_raise(self):
        value = local_ipv4()
        self.assertTrue(value is None or isinstance(value, str))

    def test_network_ready_matches_local_ipv4(self):
        self.assertEqual(network_ready(), local_ipv4() is not None)

    def test_wait_for_local_network_timeout_when_offline(self):
        config = Config()
        config.network_wait_seconds = 6
        sleeper = FakeSleeper()
        # 强制“不可用”：临时把探测函数替换掉
        import campus_login.network as network_module

        original = network_module.network_ready
        network_module.network_ready = lambda: False
        try:
            ready = wait_for_local_network(config, sleeper=sleeper, interval=3.0)
        finally:
            network_module.network_ready = original
        self.assertFalse(ready)
        self.assertTrue(sleeper.waited)

    def test_wait_for_local_network_returns_true_when_ready(self):
        config = Config()
        sleeper = FakeSleeper()
        self.assertTrue(wait_for_local_network(config, sleeper=sleeper, timeout=1))
        self.assertEqual(sleeper.waited, [])

    def test_wait_sleeps_until_ready(self):
        config = Config()
        config.network_wait_seconds = 30
        sleeper = FakeSleeper()
        import campus_login.network as network_module

        original = network_module.network_ready
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            return calls["n"] >= 3

        network_module.network_ready = flaky
        try:
            ready = wait_for_local_network(config, sleeper=sleeper, interval=2.0)
        finally:
            network_module.network_ready = original
        self.assertTrue(ready)
        self.assertEqual(sleeper.waited, [2.0, 2.0])


class ProbeTest(TempDataDirTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.portal = LocalPortal().start()
        self.addCleanup(self.portal.stop)
        self.config = Config()
        self.config.check_timeout_seconds = 3
        self.config.request_timeout_seconds = 3
        self.session = build_session(self.config)

    def test_probe_unauthenticated_redirect_is_portal(self):
        result = probe(
            self.portal.url("/generate_204"), self.session, timeout=3
        )
        self.assertEqual(result.kind, "redirect")
        self.assertIn("/portal", result.location)

    def test_probe_authenticated_returns_online(self):
        self.session.get(self.portal.url("/login"), data={}, timeout=3)
        # 先登录拿到 Cookie
        self.session.post(
            self.portal.url("/login"),
            data={"username": "2021001", "password": "test-password"},
            timeout=3,
        )
        result = probe(self.portal.url("/generate_204"), self.session, timeout=3)
        self.assertEqual(result.kind, "online")
        self.assertEqual(result.status_code, 204)

    def test_probe_connection_refused_is_network_error(self):
        result = probe("http://127.0.0.1:9/generate_204", self.session, timeout=2)
        self.assertIn(result.kind, ("network_error", "timeout"))
        self.assertTrue(result.error)

    def test_probe_content_replaced_by_portal_page(self):
        # 校园网门户有时会直接返回 200 + 登录页面（内容被替换）
        result = probe(self.portal.url("/portal"), self.session, timeout=3)
        self.assertEqual(result.kind, "portal")

    def test_check_authentication_needs_login(self):
        self.config.check_url = self.portal.url("/generate_204")
        state = check_authentication(self.config, session=self.session)
        self.assertFalse(state.authenticated)
        self.assertFalse(state.network_down)
        self.assertFalse(state.needs_manual)
        self.assertTrue(state.needs_login)
        self.assertIn("/portal", state.portal_url)
        self.assertTrue(state.details)

    def test_check_authentication_online(self):
        self.session.post(
            self.portal.url("/login"),
            data={"username": "2021001", "password": "test-password"},
            timeout=3,
        )
        self.config.check_url = self.portal.url("/generate_204")
        state = check_authentication(self.config, session=self.session)
        self.assertTrue(state.authenticated)
        self.assertIn("已认证", state.describe())

    def test_check_authentication_network_down(self):
        self.config.check_url = "http://127.0.0.1:9/generate_204"
        state = check_authentication(self.config, session=self.session, timeout=2)
        self.assertTrue(state.network_down)
        self.assertFalse(state.authenticated)
        self.assertIn("网络不可用", state.describe())

    def test_check_authentication_captcha_requires_manual(self):
        captcha_portal = LocalPortal(captcha=True).start()
        self.addCleanup(captcha_portal.stop)
        self.config.check_url = captcha_portal.url("/generate_204")
        # 让探测撞上验证码页面：直接把 check_url 指向验证码页面
        state = check_authentication(
            self.config,
            session=build_session(self.config),
            targets=[captcha_portal.url("/portal")],
        )
        self.assertTrue(state.needs_manual)
        self.assertIn("人工", state.describe())

    def test_contains_any_and_portal_detection(self):
        self.assertEqual(contains_any("请输入验证码", ["验证码"]), "验证码")
        self.assertIsNone(contains_any("一切正常", ["验证码"]))
        self.assertTrue(looks_like_portal("<form action='/login'>用户名 密码</form>"))
        self.assertFalse(looks_like_portal("ok"))

    def test_auth_state_helpers(self):
        state = AuthState(authenticated=True)
        self.assertFalse(state.needs_login)
        self.assertEqual(AuthState(network_down=True).describe(), "网络不可用")


if __name__ == "__main__":
    unittest.main()
