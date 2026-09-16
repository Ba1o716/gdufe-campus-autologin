"""核心流程（等待网络 → 检测 → 登录 → 复核 → 重试）测试。"""

from __future__ import annotations

import unittest

from campus_login.adapters import create_adapter
from campus_login.config import Config
from campus_login.credentials import Credential, MemoryCredentialStore
from campus_login.network import build_session
from campus_login.service import CampusLoginService, STATUS_TEXT, Status
from campus_login.simulation import (
    SimulatedNetwork,
    captcha_state,
    login_required_state,
    network_down_state,
    online_state,
)

from .support import LimitedSleeper, StopAfterSleeper, TempDataDirTestCase

CRED = Credential("2021001", "test-password")


class ServiceTestCase(TempDataDirTestCase):
    def build(
        self,
        adapter_name: str = "mock_success",
        script=None,
        credential: Credential | None = CRED,
        local_network: bool = True,
        sleeper=None,
        **overrides,
    ):
        config = Config()
        config.adapter = adapter_name
        config.max_retries = overrides.pop("max_retries", 3)
        config.retry_schedule_seconds = overrides.pop("retry_schedule_seconds", [1, 2, 5])
        config.startup_delay_seconds = overrides.pop("startup_delay_seconds", 0)
        config.check_interval_seconds = overrides.pop("check_interval_seconds", 30)
        for key, value in overrides.items():
            setattr(config, key, value)
        config.normalize()
        store = MemoryCredentialStore(credential)
        session = build_session(config)
        adapter = create_adapter(config, session=session)
        network = SimulatedNetwork(script if script is not None else [online_state()], local_network=local_network)
        sleeper = sleeper or StopAfterSleeper(99)
        service = CampusLoginService(config, store, adapter, network=network, sleeper=sleeper)
        return service, adapter, network, sleeper


class BasicFlowTest(ServiceTestCase):
    def test_already_online_skips_login(self):
        service, adapter, network, _ = self.build(script=[online_state()])
        self.assertIs(service.ensure_online(), Status.ONLINE)
        self.assertEqual(adapter.attempts, 0)
        self.assertEqual(service.status, Status.ONLINE)
        self.assertIn("认证成功", service.message)

    def test_login_success(self):
        service, adapter, _, _ = self.build(script=[login_required_state(), online_state()])
        # 登录接口明确返回成功 → 直接进入“认证成功”，不再用重定向去否决
        self.assertIs(service.ensure_online(), Status.AUTHENTICATED)
        self.assertEqual(adapter.attempts, 1)

    def test_network_wait_happens_before_login(self):
        service, adapter, network, _ = self.build(script=[online_state()])
        service.ensure_online()
        self.assertGreaterEqual(network.waits, 1)

    def test_login_required_without_credential(self):
        service, adapter, _, _ = self.build(script=[login_required_state()], credential=None)
        self.assertIs(service.ensure_online(), Status.FAILED)
        self.assertEqual(adapter.attempts, 0)
        self.assertIn("尚未保存", service.message)

    def test_incomplete_credential_is_rejected(self):
        service, adapter, _, _ = self.build(
            script=[login_required_state()], credential=Credential("2021001", "")
        )
        self.assertIs(service.ensure_online(), Status.FAILED)
        self.assertEqual(adapter.attempts, 0)


class RetryFlowTest(ServiceTestCase):
    def test_failure_retries_then_stops(self):
        service, adapter, _, sleeper = self.build(
            adapter_name="mock_failure", script=[login_required_state()], max_retries=3
        )
        self.assertIs(service.ensure_online(), Status.FAILED)
        self.assertEqual(adapter.attempts, 3)
        self.assertEqual(sleeper.waited, [1.0, 2.0])
        self.assertIn("3 次", service.message)

    def test_network_error_retries_with_schedule(self):
        service, adapter, _, sleeper = self.build(
            adapter_name="mock_offline",
            script=[login_required_state()],
            max_retries=4,
            retry_schedule_seconds=[2, 4],
        )
        self.assertIs(service.ensure_online(), Status.FAILED)
        self.assertEqual(sleeper.waited, [2.0, 4.0, 60.0])

    def test_network_down_does_not_try_login(self):
        service, adapter, _, _ = self.build(
            adapter_name="mock_success", script=[network_down_state()], max_retries=2
        )
        self.assertIs(service.ensure_online(), Status.FAILED)
        self.assertEqual(adapter.attempts, 0)

    def test_stop_interrupts_retry(self):
        service, adapter, _, _ = self.build(
            adapter_name="mock_failure",
            script=[login_required_state()],
            max_retries=5,
            sleeper=StopAfterSleeper(1),
        )
        self.assertIs(service.ensure_online(), Status.STOPPED)
        self.assertEqual(adapter.attempts, 1)

    def test_no_local_network_stops_early(self):
        service, adapter, _, _ = self.build(script=[login_required_state()], local_network=False)
        self.assertIs(service.ensure_online(), Status.FAILED)
        self.assertEqual(adapter.attempts, 0)
        self.assertIn("网络不可用", service.message)


class ManualInterventionTest(ServiceTestCase):
    def test_captcha_detected_by_probe_requires_manual(self):
        service, adapter, _, sleeper = self.build(script=[captcha_state()])
        self.assertIs(service.ensure_online(), Status.NEEDS_MANUAL)
        self.assertEqual(adapter.attempts, 0)
        self.assertEqual(sleeper.waited, [])
        self.assertTrue(service.message)

    def test_captcha_returned_by_login_requires_manual(self):
        service, adapter, _, _ = self.build(
            adapter_name="mock_captcha", script=[login_required_state()]
        )
        self.assertIs(service.ensure_online(), Status.NEEDS_MANUAL)
        self.assertEqual(adapter.attempts, 1)

    def test_invalid_credentials_stop_retrying(self):
        service, adapter, _, sleeper = self.build(
            adapter_name="mock_invalid", script=[login_required_state()], max_retries=5
        )
        self.assertIs(service.ensure_online(), Status.FAILED)
        self.assertEqual(adapter.attempts, 1)
        self.assertEqual(sleeper.waited, [])
        self.assertIn("账号或密码", service.message)

    def test_blocked_login_is_not_retried_automatically(self):
        service, adapter, _, _ = self.build(
            adapter_name="mock_invalid", script=[login_required_state()]
        )
        service.ensure_online()
        first_attempts = adapter.attempts
        service.ensure_online()
        self.assertEqual(adapter.attempts, first_attempts)
        self.assertIs(service.status, Status.FAILED)

    def test_reset_block_allows_login_again(self):
        service, adapter, _, _ = self.build(
            adapter_name="mock_invalid", script=[login_required_state()]
        )
        service.ensure_online()
        service.reset_block()
        service.ensure_online(ignore_block=True)
        self.assertEqual(adapter.attempts, 2)

    def test_missing_adapter_config_asks_for_real_info(self):
        service, adapter, _, sleeper = self.build(
            adapter_name="generic", script=[login_required_state()], max_retries=5
        )
        self.assertIs(service.ensure_online(), Status.FAILED)
        self.assertEqual(adapter.attempts, 1)
        self.assertEqual(sleeper.waited, [])
        self.assertIn("真实校园网请求信息", service.message)

    def test_captcha_then_manual_success(self):
        # 用户手动认证完成后，程序应能识别并回到已认证状态
        service, adapter, _, _ = self.build(script=[captcha_state(), online_state()])
        self.assertIs(service.ensure_online(), Status.NEEDS_MANUAL)
        self.assertIs(service.ensure_online(), Status.ONLINE)
        self.assertEqual(adapter.attempts, 0)


class VerificationTest(ServiceTestCase):
    def test_portal_success_is_not_overridden_by_redirect(self):
        """认证服务器确认成功时，即使状态探测仍被重定向到门户页，也判定为认证成功。"""
        service, adapter, network, sleeper = self.build(
            adapter_name="mock_success",
            script=[login_required_state()],
            max_retries=3,
        )
        self.assertIs(service.ensure_online(), Status.AUTHENTICATED)
        self.assertEqual(adapter.attempts, 1)
        self.assertEqual(sleeper.waited, [])
        # check_url 为空时不做额外的认证状态检测：只调用了一次（登录前判断是否需要认证）
        self.assertEqual(len(network.calls), 1)
        self.assertEqual(service.message, "模拟登录成功")

    def test_unknown_reply_still_uses_probe_as_positive_evidence(self):
        """响应无法识别时，才用状态探测作为“登录成功”的正向证据。"""
        service, adapter, _, sleeper = self.build(
            adapter_name="mock_unknown",
            script=[login_required_state(), online_state()],
            max_retries=2,
        )
        self.assertIs(service.ensure_online(), Status.ONLINE)
        self.assertEqual(adapter.attempts, 1)
        self.assertEqual(sleeper.waited, [])

    def test_internet_check_failure_keeps_authenticated(self):
        service, adapter, network, _ = self.build(
            adapter_name="mock_success",
            script=[login_required_state()],
            max_retries=2,
        )
        service.config.internet_check_url = "http://127.0.0.1:9/check"
        network.probe_kind = "network_error"
        self.assertIs(service.ensure_online(), Status.AUTHENTICATED)
        self.assertEqual(adapter.attempts, 1)

    def test_internet_check_success_marks_online(self):
        service, adapter, network, _ = self.build(
            adapter_name="mock_success",
            script=[login_required_state()],
        )
        service.config.internet_check_url = "http://example.invalid/generate_204"
        network.probe_kind = "online"
        events = []
        service.on_event = events.append
        self.assertIs(service.ensure_online(), Status.ONLINE)
        self.assertIn(Status.AUTHENTICATED, [event.status for event in events])
        self.assertEqual(events[-1].status, Status.ONLINE)


class RunForeverTest(ServiceTestCase):
    def test_startup_delay_and_periodic_check(self):
        service, adapter, _, sleeper = self.build(
            script=[online_state()],
            startup_delay_seconds=7,
            check_interval_seconds=33,
            sleeper=LimitedSleeper(2),
        )
        service.run_forever()
        self.assertEqual(sleeper.waited[0], 7.0)
        self.assertEqual(sleeper.waited[1], 33.0)
        self.assertIs(service.status, Status.STOPPED)

    def test_events_are_reported(self):
        events = []
        service, _, _, _ = self.build(script=[login_required_state(), online_state()])
        service.on_event = events.append
        service.ensure_online()
        statuses = [event.status for event in events]
        self.assertIn(Status.WAITING_NETWORK, statuses)
        self.assertIn(Status.CHECKING, statuses)
        self.assertIn(Status.LOGGING_IN, statuses)
        self.assertEqual(statuses[-1], Status.AUTHENTICATED)
        self.assertTrue(all(isinstance(event.message, str) for event in events))
        self.assertIn(Status.ONLINE, STATUS_TEXT)
        self.assertIn(Status.AUTHENTICATED, STATUS_TEXT)

    def test_stop_flag(self):
        service, _, _, _ = self.build(script=[online_state()])
        service.stop()
        self.assertTrue(service.stopped)
        self.assertIs(service.ensure_online(), Status.STOPPED)


if __name__ == "__main__":
    unittest.main()

