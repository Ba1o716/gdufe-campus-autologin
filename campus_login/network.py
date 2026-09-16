"""网络可用性与校园网认证状态检测。

核心原则：
  * 不用 ping 判断认证状态（校园网常常“通 DNS / 通 ICMP 但不通 HTTP”）；
  * 用 HTTP 请求判断是否被重定向到校园网认证页面；
  * 不使用高频无限请求；所有请求都有超时；所有异常都被处理。
"""

from __future__ import annotations

import logging
import socket
from dataclasses import dataclass, field
from typing import Callable, Iterable
from urllib.parse import urlparse

import requests

from .config import DEFAULT_CAPTCHA_KEYWORDS, Config, probe_urls
from .retry import Sleeper

log = logging.getLogger("campus_login.network")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 CampusLogin/1.0"
)

# 探测地址 → 期望的响应特征（状态码, 正文中应出现的内容）
EXPECTATIONS: dict[str, tuple[tuple[int, ...], str | None]] = {
    "http://connect.rom.miui.com/generate_204": ((204,), None),
    "http://connectivitycheck.platform.hicloud.com/generate_204": ((204,), None),
    "http://wifi.vivo.com.cn/generate_204": ((204,), None),
    "http://captive.apple.com/hotspot-detect.html": ((200,), "Success"),
    "http://www.msftconnecttest.com/connecttest.txt": ((200,), "Microsoft Connect Test"),
}

PORTAL_HINTS = [
    "<form",
    "login",
    "用户名",
    "帐号",
    "账号",
    "密码",
    "认证",
    "portal",
    "portal_login",
]


@dataclass
class ProbeResult:
    """一次探测的结果。"""

    url: str
    kind: str            # online / redirect / portal / http_error / network_error / timeout
    status_code: int | None = None
    location: str = ""
    final_url: str = ""
    captcha: bool = False
    snippet: str = ""
    error: str = ""

    @property
    def online(self) -> bool:
        return self.kind == "online"


@dataclass
class AuthState:
    """校园网认证状态。"""

    authenticated: bool = False
    needs_manual: bool = False
    network_down: bool = False
    portal_url: str = ""
    reason: str = ""
    details: list[dict] = field(default_factory=list)

    @property
    def needs_login(self) -> bool:
        return not self.authenticated and not self.needs_manual and not self.network_down

    def describe(self) -> str:
        if self.authenticated:
            return "已认证（可以正常上网）"
        if self.needs_manual:
            return "需要人工完成认证（验证码 / 短信验证 / 二次认证）"
        if self.network_down:
            return "网络不可用"
        return f"未认证（{self.reason or '需要登录'}）"


# ----------------------------------------------------------------------
# 本地网络（网卡 / DHCP / 路由）可用性
# ----------------------------------------------------------------------


def local_ipv4() -> str | None:
    """返回系统为出网流量选择的本机 IPv4 地址。

    用 UDP 连接一个不可路由地址来查询路由表，不会真的发包。
    没有网卡、网卡未初始化、DHCP 未完成时会抛错或返回 APIPA 地址。
    """
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(1.0)
        sock.connect(("10.255.255.255", 1))
        address = sock.getsockname()[0]
    except OSError:
        return None
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    if not address or address.startswith("127.") or address.startswith("169.254.") or address == "0.0.0.0":
        return None
    return address


def network_ready() -> bool:
    """网卡是否已经拿到可用的 IPv4 地址（DHCP 完成）。"""
    return local_ipv4() is not None


# 常见的代理 / VPN 虚拟网卡网段（Clash、Surge、Mihomo 的 TUN fake-ip 网段）。
# 这些地址不是校园网真实网卡地址，不能用来填写 wlan_user_ip。
VIRTUAL_IPV4_RANGES: tuple[tuple[str, int], ...] = (
    ("198.18.0.0", 15),
    ("198.19.0.0", 16),
)


def _in_range(address: str, network: str, prefix: int) -> bool:
    try:
        packed = socket.inet_aton(address)
        base = socket.inet_aton(network)
    except OSError:
        return False
    mask = socket.inet_aton(
        ".".join(str((0xFFFFFFFF << (32 - prefix)) >> (8 * i) & 0xFF) for i in range(3, -1, -1))
    )
    return bytes(a & m for a, m in zip(packed, mask)) == bytes(b & m for b, m in zip(base, mask))


def is_virtual_ipv4(address: str) -> bool:
    """是否属于代理 / VPN 虚拟网卡网段。"""
    return any(_in_range(address, network, prefix) for network, prefix in VIRTUAL_IPV4_RANGES)


def _usable_ipv4(address: str | None) -> bool:
    if not address:
        return False
    if address.startswith("127.") or address.startswith("169.254.") or address == "0.0.0.0":
        return False
    return True


def all_local_ipv4() -> list[str]:
    """枚举本机所有 IPv4 地址（可能包含虚拟网卡地址）。"""
    found: list[str] = []

    def add(address: str | None) -> None:
        if _usable_ipv4(address) and address not in found:
            found.append(address)

    for target in ("223.5.5.5", "119.29.29.29"):
        add(_source_ipv4_towards(target, 443))
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            add(info[4][0])
    except OSError:
        pass
    return found


def _source_ipv4_towards(host: str, port: int = 80) -> str | None:
    """查询“发往某个地址时系统选择的本机源地址”（UDP connect 不发包）。"""
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(1.0)
        sock.connect((host, port))
        return sock.getsockname()[0]
    except OSError:
        return None
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


def local_ipv4_prefer_physical(portal_host: str = "") -> str | None:
    """返回校园网网卡上的本机 IPv4，自动跳过代理 / VPN 的虚拟网卡。

    这一步很重要：开着 Clash / FlClash 之类的 TUN 代理时，默认路由会被虚拟网卡接管，
    直接取“本机 IP”会拿到 198.18.x.x 这类假地址，填进 wlan_user_ip 会导致认证失败。
    """
    toward_portal = _source_ipv4_towards(portal_host) if portal_host else None
    toward_public = _source_ipv4_towards("223.5.5.5", 443)

    # 第一优先级：真实网卡（以太网 / Wi-Fi）上的地址
    try:
        from . import winiface

        physical = winiface.physical_ipv4_addresses()
    except Exception:
        physical = []
    physical = [address for address in physical if _usable_ipv4(address)]
    if physical:
        for address in physical:
            if address in (toward_portal, toward_public):
                return address
        return physical[0]

    # 第二优先级：系统为出网流量选择的本机地址（虚拟网卡地址会被排到最后）
    fallback: list[str] = []

    def add(address: str | None) -> None:
        if _usable_ipv4(address) and address not in fallback:
            fallback.append(address)

    add(toward_portal)
    add(toward_public)
    for address in all_local_ipv4():
        add(address)

    if not fallback:
        return None
    for address in fallback:
        if not is_virtual_ipv4(address):
            return address
    return fallback[0]


def wait_for_local_network(
    config: Config,
    sleeper: Sleeper | None = None,
    on_wait: Callable[[], None] | None = None,
    timeout: float | None = None,
    interval: float = 3.0,
) -> bool:
    """等待网络适配器可用。返回 True 表示已经拿到本机 IP。"""
    sleeper = sleeper or Sleeper()
    deadline = float(timeout if timeout is not None else config.network_wait_seconds)
    waited = 0.0
    first = True
    while True:
        if network_ready():
            if not first:
                log.info("本地网络已就绪（IP：%s）", local_ipv4() or "未知")
            return True
        if waited >= deadline:
            log.warning("等待网络连接超时（已等待 %.0f 秒），未获取到可用的本机 IP", waited)
            return False
        if first:
            log.info("正在等待网络连接（网卡 / DHCP）…")
            first = False
        if on_wait:
            try:
                on_wait()
            except Exception:
                pass
        if not sleeper.sleep(interval):
            return False
        waited += interval


# ----------------------------------------------------------------------
# HTTP 探测
# ----------------------------------------------------------------------


def build_session(config: Config) -> requests.Session:
    """创建 HTTP 会话（默认不使用系统代理，遵循配置）。"""
    session = requests.Session()
    session.trust_env = bool(config.use_system_proxy)
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def verify_for(config: Config):
    """TLS 校验参数：默认开启校验；自签名证书请用 ca_bundle 指定 PEM。"""
    if config.ca_bundle.strip():
        return config.ca_bundle.strip()
    return True


def looks_like_portal(text: str) -> bool:
    if not text:
        return False
    lowered = text.lower()
    hits = sum(1 for hint in PORTAL_HINTS if hint.lower() in lowered)
    if "<form" in lowered and hits >= 1:
        return True
    return hits >= 2


def contains_any(text: str, keywords: Iterable[str]) -> str | None:
    """返回第一个命中的关键词（大小写不敏感）。"""
    if not text:
        return None
    lowered = text.lower()
    for keyword in keywords:
        if keyword and keyword.lower() in lowered:
            return keyword
    return None


def probe(
    url: str,
    session: requests.Session,
    timeout: float,
    verify=True,
    captcha_keywords: Iterable[str] = (),
) -> ProbeResult:
    """探测一个地址，判断是否可以直接访问 Internet。"""
    result = ProbeResult(url=url, kind="network_error")
    try:
        response = session.get(
            url,
            timeout=(min(timeout, 5.0), timeout),
            allow_redirects=False,
            verify=verify,
            stream=False,
        )
    except requests.exceptions.Timeout as exc:
        result.kind = "timeout"
        result.error = f"请求超时（{type(exc).__name__}）"
        return result
    except requests.exceptions.SSLError as exc:
        result.kind = "network_error"
        result.error = f"TLS 校验失败（{type(exc).__name__}）"
        return result
    except requests.exceptions.RequestException as exc:
        result.kind = "network_error"
        result.error = f"网络请求失败（{type(exc).__name__}）"
        return result
    except Exception as exc:
        result.kind = "network_error"
        result.error = f"未知错误（{type(exc).__name__}）"
        return result

    result.status_code = response.status_code
    result.final_url = str(response.url)

    body = ""
    try:
        body = response.text if response.content else ""
    except Exception:
        body = ""
    body = body[:4000]
    result.snippet = body[:200].replace("\r", " ").replace("\n", " ").strip()

    captcha = contains_any(body, list(captcha_keywords) or DEFAULT_CAPTCHA_KEYWORDS)
    result.captcha = bool(captcha)

    location = response.headers.get("Location", "") or ""
    result.location = location

    if response.status_code in (301, 302, 303, 307, 308):
        # 被劫持到认证页面（也可能跳转到 https 版探测地址，一律按“需要认证”处理，
        # 再由后续的登录流程确认）。
        if location:
            result.kind = "redirect"
        else:
            result.kind = "redirect"
            result.error = "重定向但缺少 Location"
        return result

    expected_status, expected_body = EXPECTATIONS.get(url, ((200, 204), None))
    if response.status_code not in expected_status:
        result.kind = "http_error"
        return result
    if expected_body:
        if expected_body not in body:
            # 期望内容没出现但状态码正常 → 被门户劫持 / 内容被替换
            result.kind = "portal"
            return result
    elif response.status_code == 200 and body.strip():
        # 期望空响应（generate_204）却返回了正文 → 内容被门户替换
        result.kind = "portal"
        return result

    result.kind = "online"
    return result


def check_authentication(
    config: Config,
    session: requests.Session | None = None,
    targets: list[str] | None = None,
    timeout: float | None = None,
) -> AuthState:
    """判断当前是否已经通过校园网认证。

    逻辑：
      1. 依次访问探测地址（默认使用国内手机厂商 / 苹果 / 微软的连通性检测地址）；
      2. 只要能正常访问其中一个 → 已认证；
      3. 出现 3xx 跳转 / 200 但内容被替换 → 认定被门户劫持，需要认证；
      4. 页面出现验证码等关键词 → 需要人工处理；
      5. 全部是网络错误 / 超时 → 网络不可用。
    """
    session = session or build_session(config)
    targets = targets or probe_urls(config)
    timeout = float(timeout if timeout is not None else config.check_timeout_seconds)
    verify = verify_for(config)

    state = AuthState()
    portal_url = ""
    captcha_hit = ""
    errors: list[str] = []
    kinds: list[str] = []

    for url in targets:
        result = probe(
            url,
            session,
            timeout,
            verify=verify,
            captcha_keywords=config.captcha_keywords or DEFAULT_CAPTCHA_KEYWORDS,
        )
        kinds.append(result.kind)
        state.details.append(
            {
                "url": url,
                "kind": result.kind,
                "status": result.status_code,
                "location": result.location,
                "snippet": result.snippet,
                "error": result.error,
            }
        )
        if result.kind == "online":
            state.authenticated = True
            state.reason = "探测地址可以正常访问"
            try:
                log.info("认证状态检测：已认证（%s）", urlparse(url).netloc)
            except Exception:
                log.info("认证状态检测：已认证")
            return state
        if result.kind in ("redirect", "portal"):
            if result.location and not portal_url:
                portal_url = result.location
            elif not portal_url:
                portal_url = url
        if result.captcha and not captcha_hit:
            captcha_hit = url
        if result.error:
            errors.append(f"{urlparse(url).netloc}: {result.error}")

    if captcha_hit:
        state.needs_manual = True
        state.reason = "检测到验证码 / 二次认证页面"
        state.portal_url = portal_url
        return state

    if any(kind in ("redirect", "portal") for kind in kinds):
        state.reason = "访问被重定向到校园网认证页面"
        state.portal_url = portal_url
        return state

    if all(kind in ("network_error", "timeout") for kind in kinds) and kinds:
        state.network_down = True
        state.reason = "所有探测地址都无法访问：" + ("；".join(errors[:2]) or "网络不可用")
        return state

    # 4xx / 5xx 等异常情况：不认定为已认证，交给登录流程尝试处理
    state.reason = "探测结果异常（HTTP 状态码非预期）"
    state.portal_url = portal_url
    return state
