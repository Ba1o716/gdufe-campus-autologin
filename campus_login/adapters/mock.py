"""模拟适配器：不联网也能验证核心逻辑（测试 / --selftest 使用）。"""

from __future__ import annotations

from ..credentials import Credential
from .base import BaseAdapter, LoginResult, LoginStatus


class _MockBase(BaseAdapter):
    name = "mock"
    display_name = "模拟适配器"

    def __init__(self, config, logger=None, session=None) -> None:
        super().__init__(config, logger, session)
        self.calls: list[Credential] = []

    def validate(self) -> str | None:
        return None


class MockLoginSuccessAdapter(_MockBase):
    """每次调用都返回登录成功。"""

    name = "mock_success"
    display_name = "模拟：登录成功"

    def login(self, credential: Credential) -> LoginResult:
        self.attempts += 1
        self.calls.append(credential)
        self.remember(credential)
        return LoginResult(LoginStatus.SUCCESS, "模拟登录成功", attempt=self.attempts)


class MockLoginFailureAdapter(_MockBase):
    """每次调用都返回登录失败（可重试）。"""

    name = "mock_failure"
    display_name = "模拟：登录失败"

    def login(self, credential: Credential) -> LoginResult:
        self.attempts += 1
        self.calls.append(credential)
        self.remember(credential)
        return LoginResult(LoginStatus.FAILED, "模拟登录失败", attempt=self.attempts)


class MockNetworkOfflineAdapter(_MockBase):
    """模拟网络不可用 / 服务器不可达。"""

    name = "mock_offline"
    display_name = "模拟：网络不可用"

    def login(self, credential: Credential) -> LoginResult:
        self.attempts += 1
        self.calls.append(credential)
        self.remember(credential)
        return LoginResult(LoginStatus.NETWORK_ERROR, "模拟网络不可用", attempt=self.attempts)


class MockCaptchaRequiredAdapter(_MockBase):
    """模拟需要验证码 / 二次认证。"""

    name = "mock_captcha"
    display_name = "模拟：需要验证码"

    def login(self, credential: Credential) -> LoginResult:
        self.attempts += 1
        self.calls.append(credential)
        self.remember(credential)
        return LoginResult(
            LoginStatus.CAPTCHA_REQUIRED, "模拟：检测到验证码，需要手动完成认证", attempt=self.attempts
        )


class MockInvalidCredentialsAdapter(_MockBase):
    """模拟账号密码错误。"""

    name = "mock_invalid"
    display_name = "模拟：账号密码错误"

    def login(self, credential: Credential) -> LoginResult:
        self.attempts += 1
        self.calls.append(credential)
        self.remember(credential)
        return LoginResult(
            LoginStatus.INVALID_CREDENTIALS, "模拟：校园网账号或密码可能错误", attempt=self.attempts
        )


class MockUnknownResponseAdapter(_MockBase):
    """模拟“响应无法识别”（接口格式变化），由状态探测决定最终结果。"""

    name = "mock_unknown"
    display_name = "模拟：响应无法识别"

    def login(self, credential: Credential) -> LoginResult:
        self.attempts += 1
        self.calls.append(credential)
        self.remember(credential)
        return LoginResult(
            LoginStatus.BAD_RESPONSE,
            "认证接口响应格式发生变化，无法判断登录结果（将用认证状态探测复核）",
            attempt=self.attempts,
        )
