"""模拟网络：不联网也能跑完整流程（测试 / --selftest 使用）。"""

from __future__ import annotations

import requests

from .config import Config
from .network import AuthState, ProbeResult
from .retry import Sleeper


def online_state(reason: str = "模拟：已认证") -> AuthState:
    return AuthState(authenticated=True, reason=reason)


def login_required_state(portal_url: str = "http://portal.local/login") -> AuthState:
    return AuthState(reason="模拟：需要认证", portal_url=portal_url)


def captcha_state(portal_url: str = "http://portal.local/login") -> AuthState:
    return AuthState(needs_manual=True, reason="模拟：检测到验证码", portal_url=portal_url)


def network_down_state(reason: str = "模拟：网络不可用") -> AuthState:
    return AuthState(network_down=True, reason=reason)


class SimulatedNetwork:
    """按脚本返回认证状态，用于在没有校园网的环境下验证程序逻辑。

    script 中最后一个状态会被一直重复。
    """

    def __init__(
        self,
        script: list[AuthState] | None = None,
        local_network: bool = True,
        local_network_after: int = 0,
        probe_kind: str = "online",
    ) -> None:
        self.script = list(script or [online_state()])
        self.local_network = local_network
        self.local_network_after = local_network_after
        self.probe_kind = probe_kind
        self.calls: list[str] = []
        self.waits = 0

    # --- 与 network 模块同名的接口 ---
    def build_session(self, config: Config) -> requests.Session:
        session = requests.Session()
        session.trust_env = True
        return session

    def wait_for_local_network(
        self,
        config: Config,
        sleeper: Sleeper | None = None,
        on_wait=None,
        timeout: float | None = None,
        interval: float = 3.0,
    ) -> bool:
        self.waits += 1
        if self.local_network_after and self.waits < self.local_network_after:
            if on_wait:
                on_wait()
            return False
        return bool(self.local_network)

    def check_authentication(self, config, session=None, targets=None, timeout=None) -> AuthState:
        index = len(self.calls)
        self.calls.append("check")
        if index >= len(self.script):
            index = len(self.script) - 1
        state = self.script[index]
        return AuthState(
            authenticated=state.authenticated,
            needs_manual=state.needs_manual,
            network_down=state.network_down,
            portal_url=state.portal_url,
            reason=state.reason,
            details=list(state.details),
        )

    def probe(self, url, session=None, timeout=None, verify=True, **kwargs) -> ProbeResult:
        """模拟“互联网连通性检测”用的单地址探测。"""
        return ProbeResult(
            url=url,
            kind=self.probe_kind,
            status_code=204 if self.probe_kind == "online" else None,
            error="" if self.probe_kind == "online" else f"模拟：{self.probe_kind}",
        )
