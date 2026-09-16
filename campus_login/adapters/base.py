"""认证适配器基类。

校园网认证方式五花八门（Web Portal、HTTP POST、JSON API、需要 JS 生成 Token……），
所以程序把“怎么登录”隔离在 Adapter 里：
以后学校换了接口，只改 / 只加一个 Adapter，其它代码不动。
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from urllib.parse import parse_qs, urlsplit

import requests

from ..config import Config
from ..credentials import Credential
from ..network import build_session, local_ipv4_prefer_physical, verify_for


class LoginStatus(str, Enum):
    SUCCESS = "success"                       # 登录成功
    INVALID_CREDENTIALS = "invalid_credentials"  # 账号或密码错误（不再快速重试）
    CAPTCHA_REQUIRED = "captcha_required"     # 需要验证码 / 二次认证（转人工）
    NOT_CONFIGURED = "not_configured"         # 缺少真实接口信息，无法登录
    SERVER_ERROR = "server_error"             # 校园网服务器错误（稍后重试）
    BAD_RESPONSE = "bad_response"             # 响应无法识别（接口可能变了）
    NETWORK_ERROR = "network_error"           # 网络问题（稍后重试）
    FAILED = "failed"                         # 明确失败（可重试）

    @property
    def retryable(self) -> bool:
        return self in (
            LoginStatus.SERVER_ERROR,
            LoginStatus.BAD_RESPONSE,
            LoginStatus.NETWORK_ERROR,
            LoginStatus.FAILED,
        )

    @property
    def needs_manual(self) -> bool:
        return self in (LoginStatus.CAPTCHA_REQUIRED, LoginStatus.NOT_CONFIGURED)


@dataclass
class LoginResult:
    status: LoginStatus
    message: str = ""
    detail: dict[str, Any] = field(default_factory=dict)
    attempt: int = 0

    @property
    def ok(self) -> bool:
        return self.status is LoginStatus.SUCCESS


class BaseAdapter:
    """认证适配器接口。"""

    name = "base"
    display_name = "基础适配器"
    description = ""

    # 登录接口返回 JSON / JSONP 时，哪个值代表“认证服务器已接受登录”。
    # ePortal（深澜 / 城市热点）用 result=1 表示成功。
    json_success_codes: tuple[int, ...] = (1,)

    def __init__(
        self,
        config: Config,
        logger: logging.Logger | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.config = config
        self.log = logger or logging.getLogger(f"campus_login.adapter.{self.name}")
        self.session = session or build_session(config)
        self.credential = Credential()
        self.attempts = 0

    # --------------------------------------------------------------
    # 子类需要实现
    # --------------------------------------------------------------
    def login(self, credential: Credential) -> LoginResult:
        raise NotImplementedError

    def validate(self) -> str | None:
        """检查配置是否足够。返回 None 表示可以尝试登录。"""
        return None

    # --------------------------------------------------------------
    # 公共工具
    # --------------------------------------------------------------
    @property
    def portal_url(self) -> str:
        """校园网登录页面地址（用于人工登录 Windows 浏览器）。"""
        return (self.config.portal_url or self.config.login_url or "").strip()

    def remember(self, credential: Credential) -> None:
        """记住最近使用的凭据，用于日志脱敏。"""
        self.credential = credential

    def transform_password(self, password: str) -> str:
        """按配置对密码做预处理（部分校园网需要先散列再提交）。"""
        value = f"{password}{self.config.password_salt_suffix or ''}"
        transform = self.config.password_transform
        if transform == "none" or not transform:
            return password if not self.config.password_salt_suffix else value
        algo = {"md5": hashlib.md5, "sha1": hashlib.sha1, "sha256": hashlib.sha256}.get(transform)
        if algo is None:
            return value
        digest = algo(value.encode("utf-8")).hexdigest()
        return digest.upper() if self.config.password_digest_upper else digest

    def build_payload(self, username: str, password: str) -> dict[str, str]:
        """构造认证请求参数（字段名全部来自配置，不硬编码任何学校的接口）。"""
        payload: dict[str, str] = {
            str(k): self.expand_placeholders(str(v))
            for k, v in (self.config.extra_fields or {}).items()
        }
        username_field = (self.config.username_field or "username").strip() or "username"
        password_field = (self.config.password_field or "password").strip() or "password"
        # ePortal 等接口会在账号前加一段固定前缀（例如 ",0,"），按配置拼接
        prefix = self.expand_placeholders(self.config.username_prefix or "")
        payload[username_field] = f"{prefix}{username}"
        payload[password_field] = self.transform_password(password)
        service_field = (self.config.service_field or "").strip()
        if service_field and self.config.service_value:
            payload[service_field] = self.config.service_value
        return payload

    def expand_placeholders(self, value: str) -> str:
        """展开附加参数里的占位符（让 wlan_user_ip 这类参数每次自动取当前值）。

        {local_ip}      本机在校园网网卡上的 IPv4（自动跳过 Clash / VPN 的虚拟网卡地址）
        {portal_ac_ip}  从“校园网登录页面 URL”的 wlanacip 参数里取出你当前所连 AC 的地址
        {random}        1~9999 的随机数（ePortal 的 v 参数这类防缓存字段用）
        """
        if not isinstance(value, str) or "{" not in value:
            return value
        text = value
        if "{local_ip}" in text:
            host = ""
            try:
                host = urlsplit(self.config.login_url or "").hostname or ""
            except ValueError:
                host = ""
            text = text.replace("{local_ip}", local_ipv4_prefer_physical(host) or "")
        if "{portal_ac_ip}" in text:
            text = text.replace("{portal_ac_ip}", self.portal_ac_ip())
        if "{random}" in text:
            text = text.replace("{random}", str(random.randint(1, 9999)))
        return text

    def portal_ac_ip(self) -> str:
        """返回本机所连 AC 的地址。

        校园网门户页面通常带 wlanacip 参数（例如 a79.htm?wlanacip=100.64.13.18），
        不同楼栋 / 不同 AC 的值可能不同；取不到时退回适配器内置的默认值。
        """
        for url in (self.config.portal_url, self.config.login_url):
            if not url:
                continue
            try:
                query = parse_qs(urlsplit(url).query)
            except ValueError:
                continue
            for key in ("wlanacip", "wlan_ac_ip", "nasip"):
                values = query.get(key)
                if values and values[0]:
                    return values[0]
        return self.DEFAULT_AC_IP

    def classify(self, status_code: int, body: str, location: str = "") -> tuple[LoginStatus, str]:
        """根据响应内容推断登录结果。

        注意：这里的判断只是“初步判断”，服务层还会用认证状态探测复核最终结果。
        """
        from ..network import contains_any

        # 1) 先按 JSON / JSONP 的 result 字段判断（ePortal 的 dr1003({...}) 在这里处理）
        data, wrapped = extract_jsonp(body)
        if data is not None and (wrapped or "result" in data):
            verdict = self.classify_json(data)
            if verdict is not None:
                return verdict

        # 2) 再退回关键词判断
        hit = contains_any(body, self.config.captcha_keywords)
        if hit:
            return LoginStatus.CAPTCHA_REQUIRED, f"响应中出现“{hit}”，需要人工完成认证"

        hit = contains_any(body, self.config.invalid_credential_keywords)
        if hit:
            return LoginStatus.INVALID_CREDENTIALS, "校园网账号或密码可能错误，请检查配置"

        hit = contains_any(body, self.config.success_keywords)
        if hit:
            return LoginStatus.SUCCESS, f"Portal 认证成功（响应中出现“{hit}”）"

        hit = contains_any(body, self.config.failure_keywords)
        if hit:
            return LoginStatus.FAILED, f"认证接口返回失败标志（{hit}）"

        if status_code >= 500:
            return LoginStatus.SERVER_ERROR, f"校园网认证服务器错误（HTTP {status_code}）"
        if status_code >= 400:
            return LoginStatus.BAD_RESPONSE, f"认证接口返回 HTTP {status_code}（接口可能已变化）"
        if 300 <= status_code < 400:
            return (
                LoginStatus.BAD_RESPONSE,
                f"认证接口返回重定向（{location or '无 Location'}），结果未知，将用认证状态复核",
            )

        return (
            LoginStatus.BAD_RESPONSE,
            "认证接口响应格式发生变化，无法判断登录结果（将用认证状态探测复核）",
        )

    def classify_json(self, data: dict) -> tuple[LoginStatus, str] | None:
        """按 JSON 里的 result / msg 判断登录结果（ePortal 协议）。

        result == 1                 → 认证服务器已接受登录（Portal 认证成功）
        result != 1 且 msg 含验证码  → 需要人工完成认证
        result != 1 且 msg 含密码错  → 账号或密码错误（不再快速重试）
        result != 1 且 msg 说已在线  → 视为认证成功
        其它                        → 认证失败，并把 msg 原样带出来
        """
        from ..network import contains_any

        code_raw = None
        for key in ("result", "code", "ret"):
            if key in data:
                code_raw = data[key]
                break
        if code_raw is None:
            return None
        try:
            code = int(str(code_raw).strip())
        except (TypeError, ValueError):
            return None

        message = ""
        for key in ("msg", "message", "info", "errmsg"):
            value = data.get(key)
            if value:
                message = str(value).strip()
                break
        suffix = f"：{message}" if message else ""

        if code in self.json_success_codes:
            return LoginStatus.SUCCESS, f"Portal 认证成功{suffix}"

        raw_text = message or json.dumps(data, ensure_ascii=False)
        hit = contains_any(raw_text, self.config.captcha_keywords)
        if hit:
            return LoginStatus.CAPTCHA_REQUIRED, f"认证接口要求人工验证（{message or hit}）"
        hit = contains_any(raw_text, self.config.invalid_credential_keywords)
        if hit:
            return LoginStatus.INVALID_CREDENTIALS, f"校园网账号或密码可能错误{suffix}"
        hit = contains_any(raw_text, ("已在线", "已登录", "已经登录", "already online"))
        if hit:
            return LoginStatus.SUCCESS, f"账号已在线，视为认证成功{suffix}"
        return LoginStatus.FAILED, f"Portal 认证失败{suffix}（result={code}）"

    def snippet(self, text: str, limit: int = 200) -> str:
        """生成用于日志/诊断的简短片段（已脱敏，不含密码）。"""
        if not text:
            return ""
        cleaned = " ".join(text.split())
        cleaned = self.credential.redact(cleaned) or cleaned
        return cleaned[:limit]

    def verify_kwargs(self) -> dict[str, Any]:
        return {"verify": verify_for(self.config)}

    def describe(self) -> str:
        return self.display_name or self.name


def extract_jsonp(text: str) -> tuple[dict | None, bool]:
    """解析 JSON / JSONP 响应，返回 (数据, 是否是 JSONP 包装)。

    支持这些形态：
        {"result":1,"msg":"Portal协议认证成功！"}
        dr1003({"result":1,"msg":"Portal协议认证成功！"});
        jsonpCallback && jsonpCallback({"result":1});
    """
    if not text:
        return None, False
    cleaned = text.strip().lstrip("\ufeff").strip()
    if not cleaned:
        return None, False

    candidates: list[str] = []
    wrapped = False
    if cleaned[0] in "{[":
        candidates.append(cleaned)
    start, end = cleaned.find("("), cleaned.rfind(")")
    if start != -1 and end > start:
        inner = cleaned[start + 1 : end].strip()
        if inner:
            wrapped = True
            candidates.append(inner)
    first, last = cleaned.find("{"), cleaned.rfind("}")
    if first != -1 and last > first:
        candidates.append(cleaned[first : last + 1])

    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict):
            return data, wrapped
    return None, False
