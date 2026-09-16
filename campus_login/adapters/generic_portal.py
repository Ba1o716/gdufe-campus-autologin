"""通用校园网认证适配器（Web Portal / HTTP POST 登录）。

这个适配器不假设任何学校的接口，全部通过配置提供：
  * 登录请求地址（login_url）
  * 请求方法（POST / GET）
  * 参数格式（form / json）
  * 账号、密码、运营商（service）参数的字段名
  * 附加固定参数（extra_fields）
  * 是否需要先对密码做散列（password_transform）

如果页面/响应里出现无法自动处理的验证码、短信验证、二次认证，会直接转人工提示。
"""

from __future__ import annotations

import re

import requests

from ..config import Config
from ..credentials import Credential
from ..network import contains_any, verify_for
from .base import BaseAdapter, LoginResult, LoginStatus

# 只在“表单元素自身”的属性里找验证码关键词，避免把正文里的“验证码”误判成验证码要求
_FORM_TAG_PATTERN = re.compile(r"<(input|select|textarea|img|iframe)\b[^>]*>", re.IGNORECASE)
_ATTR_PATTERN = re.compile(r'\b(name|id|placeholder|alt|src|onclick|class)\s*=\s*["\']([^"\']*)["\']', re.IGNORECASE)


def detect_captcha_field(html: str, keywords) -> str | None:
    """页面里是否存在“验证码输入框/验证码图片”。返回命中的关键词或 None。"""
    if not html:
        return None
    for match in _FORM_TAG_PATTERN.finditer(html):
        for _, value in _ATTR_PATTERN.findall(match.group(0)):
            hit = contains_any(value, keywords)
            if hit:
                return hit
    return None


def _safe_url(url: str) -> str:
    """日志里只保留 协议://主机/路径，避免把 GET 查询串里的敏感参数写进日志。"""
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}" if parts.scheme else url


class GenericPortalAdapter(BaseAdapter):
    name = "generic"
    display_name = "通用网页认证（HTTP 表单 / JSON 接口）"
    description = "按照配置构造登录请求，适用于绝大多数 Web Portal 认证方式。"

    # 下面的常量只作为“配置为空时的默认值”，不是任何具体学校的接口。
    LOGIN_URL = ""
    CHECK_URL = ""
    USERNAME_FIELD = "username"
    PASSWORD_FIELD = "password"
    SERVICE_FIELD = "service"

    def __init__(self, config: Config, logger=None, session=None) -> None:
        super().__init__(config, logger, session)
        # 适配器预留常量只在配置为空时生效
        if not self.config.login_url and self.LOGIN_URL:
            self.config.login_url = self.LOGIN_URL
        if not self.config.check_url and self.CHECK_URL:
            self.config.check_url = self.CHECK_URL
        if self.config.username_field in ("", None):
            self.config.username_field = self.USERNAME_FIELD
        if self.config.password_field in ("", None):
            self.config.password_field = self.PASSWORD_FIELD
        if self.config.service_field in ("", None):
            self.config.service_field = self.SERVICE_FIELD
        # ePortal（深澜 / 城市热点）的 /eportal/portal/login 是 GET 接口。
        # 配置里若仍是默认的 POST，这里自动改成 GET，避免参数被放进请求体导致认证失败。
        if (
            (self.config.request_method or "").upper() == "POST"
            and "eportal" in (self.config.login_url or "").lower()
        ):
            self.log.info("检测到 ePortal 登录接口（%s），自动改用 GET 请求", _safe_url(self.config.login_url))
            self.config.request_method = "GET"

    # ------------------------------------------------------------------
    def validate(self) -> str | None:
        if not self.config.login_url.strip():
            return (
                "这里需要真实校园网请求信息才能继续：尚未填写“登录请求地址（login_url）”。"
                "请用浏览器开发者工具抓取登录请求，或运行 --discover 分析登录页面。"
            )
        if not self.config.username_field.strip():
            return "尚未填写账号参数字段名（username_field）"
        if not self.config.password_field.strip():
            return "尚未填写密码参数字段名（password_field）"
        return None

    # ------------------------------------------------------------------
    def login(self, credential: Credential) -> LoginResult:
        self.remember(credential)
        self.attempts += 1
        attempt = self.attempts

        problem = self.validate()
        if problem:
            return LoginResult(LoginStatus.NOT_CONFIGURED, problem, attempt=attempt)

        if not credential.complete:
            return LoginResult(
                LoginStatus.NOT_CONFIGURED,
                "尚未保存校园网账号或密码，请先运行 --credential 或打开设置界面填写",
                attempt=attempt,
            )

        # 提交密码之前先看一眼登录页面：如果页面要求验证码，就不提交密码，直接转人工
        if self.config.captcha_precheck and (
            self.config.portal_url.strip() or self.config.login_url.strip()
        ):
            captcha = self._precheck_captcha()
            if captcha:
                return LoginResult(LoginStatus.CAPTCHA_REQUIRED, captcha, attempt=attempt)

        payload = self.build_payload(credential.username, credential.password)
        timeout = float(self.config.request_timeout_seconds)
        verify = verify_for(self.config)
        url = self.config.login_url.strip()

        try:
            if self.config.request_method == "GET":
                response = self.session.get(
                    url, params=payload, timeout=(min(timeout, 5.0), timeout),
                    allow_redirects=False, verify=verify,
                )
            elif self.config.content_type == "json":
                response = self.session.post(
                    url, json=payload, timeout=(min(timeout, 5.0), timeout),
                    allow_redirects=False, verify=verify,
                )
            else:
                response = self.session.post(
                    url, data=payload, timeout=(min(timeout, 5.0), timeout),
                    allow_redirects=False, verify=verify,
                )
        except requests.exceptions.Timeout:
            return LoginResult(LoginStatus.NETWORK_ERROR, "登录请求超时", attempt=attempt)
        except requests.exceptions.SSLError:
            return LoginResult(
                LoginStatus.NETWORK_ERROR,
                "登录请求 HTTPS 证书校验失败（如为自签名证书，请在配置中指定 ca_bundle）",
                attempt=attempt,
            )
        except requests.exceptions.RequestException as exc:
            return LoginResult(
                LoginStatus.NETWORK_ERROR,
                f"登录请求失败（{type(exc).__name__}）",
                attempt=attempt,
            )
        except Exception as exc:
            return LoginResult(
                LoginStatus.NETWORK_ERROR,
                f"登录请求出现未预期错误（{type(exc).__name__}）",
                attempt=attempt,
            )

        body, final_url, location = self._resolve_body(response, verify=verify, timeout=timeout)
        self.log.info(
            "登录请求已发送（%s %s，HTTP %s）",
            self.config.request_method,
            _safe_url(url),
            response.status_code,
        )
        status, message = self.classify(response.status_code, body, location)
        detail = {
            "http_status": response.status_code,
            "final_url": final_url,
            "snippet": self.snippet(body),
        }
        if status is LoginStatus.BAD_RESPONSE:
            self.log.warning("认证接口响应格式发生变化或无法识别：HTTP %s", response.status_code)
        return LoginResult(status, message, detail=detail, attempt=attempt)

    # ------------------------------------------------------------------
    def _precheck_captcha(self) -> str | None:
        """登录前检查登录页面是否要求验证码（不提交密码）。"""
        url = (self.config.portal_url or self.config.login_url).strip()
        if not url:
            return None
        try:
            response = self.session.get(
                url,
                timeout=(min(self.config.check_timeout_seconds, 5.0), self.config.check_timeout_seconds),
                allow_redirects=True,
                verify=verify_for(self.config),
            )
        except Exception as exc:
            self.log.debug("登录页面预检查失败（忽略）：%s", type(exc).__name__)
            return None
        if not 200 <= response.status_code < 400:
            return None
        body = (response.text or "")[:8000]
        hit = detect_captcha_field(body, self.config.captcha_keywords)
        if hit:
            return f"登录页面出现“{hit}”，无法自动完成，需要手动完成认证"
        return None

    def _resolve_body(self, response, verify, timeout: float):
        """跟随最多 3 次重定向，拿到最终页面内容（很多门户登录成功会 302）。"""
        body = ""
        try:
            body = response.text or ""
        except Exception:
            body = ""
        location = response.headers.get("Location", "") or ""
        final_url = str(response.url)
        hops = 0
        while 300 <= response.status_code < 400 and location and hops < 3:
            target = requests.compat.urljoin(final_url, location)
            try:
                response = self.session.get(
                    target,
                    timeout=(min(timeout, 5.0), timeout),
                    allow_redirects=False,
                    verify=verify,
                )
            except Exception as exc:
                self.log.debug("跟随重定向失败（忽略）：%s", type(exc).__name__)
                break
            final_url = str(response.url)
            location = response.headers.get("Location", "") or ""
            try:
                body = response.text or ""
            except Exception:
                body = ""
            hops += 1
        return body[:8000], final_url, location
