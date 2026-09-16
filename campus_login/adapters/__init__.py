"""认证适配器注册表。

要支持新的校园网：新建一个模块 + 在这里注册即可，其它代码无需修改。
"""

from __future__ import annotations

from typing import Type

from ..config import Config
from .base import BaseAdapter, LoginResult, LoginStatus
from .eportal import EPortalAdapter
from .gdufe import GuangDongUniversityOfFinanceAdapter
from .generic_portal import GenericPortalAdapter
from .mock import (
    MockCaptchaRequiredAdapter,
    MockInvalidCredentialsAdapter,
    MockLoginFailureAdapter,
    MockLoginSuccessAdapter,
    MockNetworkOfflineAdapter,
    MockUnknownResponseAdapter,
)

ADAPTERS: dict[str, Type[BaseAdapter]] = {
    "generic": GenericPortalAdapter,
    "eportal": EPortalAdapter,
    "gdufe": GuangDongUniversityOfFinanceAdapter,
    "mock_success": MockLoginSuccessAdapter,
    "mock_failure": MockLoginFailureAdapter,
    "mock_offline": MockNetworkOfflineAdapter,
    "mock_captcha": MockCaptchaRequiredAdapter,
    "mock_invalid": MockInvalidCredentialsAdapter,
    "mock_unknown": MockUnknownResponseAdapter,
}


def create_adapter(config: Config, logger=None, session=None) -> BaseAdapter:
    cls = ADAPTERS.get(config.adapter, GenericPortalAdapter)
    return cls(config, logger=logger, session=session)


__all__ = [
    "ADAPTERS",
    "BaseAdapter",
    "LoginResult",
    "LoginStatus",
    "GenericPortalAdapter",
    "EPortalAdapter",
    "GuangDongUniversityOfFinanceAdapter",
    "MockLoginSuccessAdapter",
    "MockLoginFailureAdapter",
    "MockNetworkOfflineAdapter",
    "MockCaptchaRequiredAdapter",
    "MockInvalidCredentialsAdapter",
    "MockUnknownResponseAdapter",
    "create_adapter",
]
