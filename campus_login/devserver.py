"""本机“假校园网门户”：用于离线自检（--selftest）和自动化测试。

它模拟的是最常见的 Web Portal 认证方式：
  * 未认证时，访问 generate_204 会被 302 重定向到 /portal
  * 已认证时，generate_204 返回 204
  * /login 接受 username / password（form 表单）

这个服务只监听 127.0.0.1，只在你自己电脑上运行，不涉及任何真实校园网。
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PORTAL_HTML = """<!DOCTYPE html>
<html><head><title>校园网认证</title></head>
<body>
<h2>请登录校园网</h2>
<form action="/login" method="post">
  <input type="text" name="username" placeholder="用户名">
  <input type="password" name="password" placeholder="密码">
  <select name="service"><option value="campus">校园网</option></select>
  <input type="submit" value="登录">
</form>
</body></html>
"""

CAPTCHA_HTML = """<!DOCTYPE html>
<html><head><title>校园网认证</title></head>
<body>
<h2>请登录校园网</h2>
<form action="/login" method="post">
  <input type="text" name="username">
  <input type="password" name="password">
  <input type="text" name="captcha" placeholder="验证码">
  <img src="/captcha.png" alt="验证码">
  <input type="submit" value="登录">
</form>
</body></html>
"""


class PortalHandler(BaseHTTPRequestHandler):
    server_version = "CampusLoginTestPortal/1.0"

    # 让测试输出干净一些
    def log_message(self, fmt, *args):  # noqa: D102
        if getattr(self.server, "verbose", False):
            super().log_message(fmt, *args)

    # ------------------------------------------------------------------
    def _authenticated(self) -> bool:
        cookie = self.headers.get("Cookie", "") or ""
        return "portal_auth=1" in cookie

    def _send(self, code: int, body: str = "", content_type: str = "text/html; charset=utf-8", headers=None):
        payload = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if payload:
            self.wfile.write(payload)

    def _portal_html(self) -> str:
        html = CAPTCHA_HTML if self.server.captcha else PORTAL_HTML
        return html

    # ------------------------------------------------------------------
    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        if path in ("/generate_204", "/gen204"):
            if self._authenticated() and not self.server.probe_still_hijacked:
                self._send(204)
            else:
                self._send(302, headers={"Location": f"/portal?url={self.path}"})
            return
        if path.startswith("/eportal/portal/login"):
            # 模拟 ePortal（深澜 / 城市热点）的 JSONP 登录接口
            form = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            # 真实 ePortal 会接受 ",0,账号" 这种带前缀的账号，这里按同样规则解析
            raw_account = form.get(self.server.username_field, "")
            username = raw_account.split(",")[-1] if "," in raw_account else raw_account
            password = form.get(self.server.password_field, "")
            if self.server.server_error:
                self._send(500, "internal error")
                return
            if username == self.server.username and password == self.server.password:
                body = 'dr1003({"result":1,"msg":"Portal协议认证成功！"});'
                self._send(
                    200,
                    body,
                    content_type="text/javascript; charset=utf-8",
                    headers={"Set-Cookie": "portal_auth=1; Path=/"},
                )
                return
            self._send(
                200,
                'dr1003({"result":0,"msg":"用户名或密码错误"});',
                content_type="text/javascript; charset=utf-8",
            )
            return
        if path in ("/portal", "/captive.html", "/"):
            params = parse_qs(parsed.query)
            if params.get("result", [""])[0] == "ok":
                # 登录成功后 302 跳转到的“认证成功”页面
                self._send(
                    200, "登录成功", headers={"Set-Cookie": "portal_auth=1; Path=/"}
                )
                return
            self._send(200, self._portal_html())
            return
        if path == "/captcha.png":
            self._send(200, "fake", content_type="image/png")
            return
        if path == "/check":
            if self._authenticated():
                self._send(200, '{"code":0,"message":"ok"}', content_type="application/json")
            else:
                self._send(302, headers={"Location": "/portal"})
            return
        self._send(404, "not found")

    def do_POST(self):  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != "/login":
            self._send(404, "not found")
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        form = {k: v[0] for k, v in parse_qs(raw).items()}

        if self.server.captcha:
            self._send(200, "请输入验证码后重试")
            return

        if self.server.server_error:
            self._send(500, "internal error")
            return

        username = form.get(self.server.username_field, "")
        password = form.get(self.server.password_field, "")
        service_value = form.get("service", "")
        service_ok = (
            not self.server.required_service
            or service_value == self.server.required_service
        )
        if username == self.server.username and password == self.server.password and not service_ok:
            self._send(200, "认证失败：服务类型错误")
            return
        if username == self.server.username and password == self.server.password:
            if self.server.redirect_on_success:
                self._send(
                    302,
                    headers={
                        "Location": "/portal?result=ok",
                        "Set-Cookie": "portal_auth=1; Path=/",
                    },
                )
                return
            self._send(
                200,
                self.server.success_text,
                headers={"Set-Cookie": "portal_auth=1; Path=/"},
            )
            return
        self._send(200, "账号或密码错误")


class LocalPortal:
    """在 127.0.0.1 上启动的假校园网门户。"""

    def __init__(
        self,
        username: str = "2021001",
        password: str = "test-password",
        username_field: str = "username",
        password_field: str = "password",
        captcha: bool = False,
        redirect_on_success: bool = False,
        server_error: bool = False,
        success_text: str = "登录成功",
        required_service: str | None = None,
        eportal: bool = False,
        probe_still_hijacked: bool = False,
    ) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), PortalHandler)
        self.server.username = username
        self.server.password = password
        self.server.username_field = username_field
        self.server.password_field = password_field
        self.server.captcha = captcha
        self.server.redirect_on_success = redirect_on_success
        self.server.server_error = server_error
        self.server.success_text = success_text
        self.server.required_service = required_service
        self.server.eportal = eportal
        self.server.probe_still_hijacked = probe_still_hijacked
        self.server.verbose = False
        self.thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return int(self.server.server_address[1])

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def start(self) -> "LocalPortal":
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def stop(self) -> None:
        try:
            self.server.shutdown()
        except Exception:
            pass
        try:
            self.server.server_close()
        except Exception:
            pass

    def __enter__(self) -> "LocalPortal":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()
