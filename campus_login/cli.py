"""命令行接口。

用法示例：
    CampusLogin.exe --status        查看当前认证状态
    CampusLogin.exe --login         立即执行一次自动登录
    CampusLogin.exe --settings      打开设置界面
    CampusLogin.exe --install       设置开机自动运行
    CampusLogin.exe --uninstall     取消开机自动运行
    CampusLogin.exe --discover URL  分析校园网登录页面（不提交密码）
    CampusLogin.exe --selftest      离线自检（不需要真实校园网）
"""

from __future__ import annotations

import argparse
import json
import sys
from html.parser import HTMLParser
from urllib.parse import urljoin

import requests

from . import APP_DISPLAY_NAME, APP_VERSION
from .adapters import create_adapter
from .adapters.generic_portal import detect_captcha_field
from .app import Application, ensure_credential_interactive, message_box
from .autostart import AutostartError, AutostartManager
from .config import Config, load_config, save_config
from .credentials import Credential, MemoryCredentialStore, create_store
from .devserver import LocalPortal
from .instance import SingleInstance
from .logging_setup import setup_logging
from .network import USER_AGENT, build_session, check_authentication
from .paths import config_path, log_path
from .retry import FakeSleeper
from .service import CampusLoginService, STATUS_TEXT, Status
from .simulation import (
    SimulatedNetwork,
    captcha_state,
    login_required_state,
    network_down_state,
    online_state,
)

EXIT_OK = 0
EXIT_NOT_ONLINE = 1
EXIT_NEEDS_MANUAL = 2
EXIT_CONFIG_ERROR = 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="CampusLogin",
        description=f"{APP_DISPLAY_NAME} v{APP_VERSION}（本地校园网自动登录；仅用于本人账号的正常登录）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  --status      查看认证状态\n"
            "  --login       立即自动登录\n"
            "  --settings    打开设置界面\n"
            "  --install     设置开机自动运行\n"
            "  --selftest    离线自检（不联网）\n"
        ),
    )
    actions = parser.add_argument_group("动作（默认启动托盘后台）")
    actions.add_argument("--tray", action="store_true", help="托盘后台运行（开机启动使用）")
    actions.add_argument("--quit", action="store_true", help="让正在运行的后台程序退出")
    actions.add_argument("--daemon", action="store_true", help="前台运行，控制台输出，Ctrl+C 退出")
    actions.add_argument("--status", action="store_true", help="打印当前认证状态")
    actions.add_argument("--check", action="store_true", help="立即检测一次认证状态")
    actions.add_argument("--login", action="store_true", help="立即执行一次完整登录流程")
    actions.add_argument("--settings", action="store_true", help="打开设置界面")
    actions.add_argument("--credential", action="store_true", help="交互式设置校园网账号和密码")
    actions.add_argument("--install", action="store_true", help="设置开机自动运行（幂等）")
    actions.add_argument("--uninstall", action="store_true", help="取消开机自动运行（幂等）")
    actions.add_argument("--discover", metavar="URL", help="分析校园网登录页面（不提交密码）")
    actions.add_argument("--selftest", action="store_true", help="离线自检（使用本机模拟门户）")

    other = parser.add_argument_group("其它")
    other.add_argument("--config", action="store_true", help="打印配置文件路径与内容")
    other.add_argument("--log-path", action="store_true", help="打印日志文件路径")
    other.add_argument("--tail", type=int, metavar="N", help="打印日志最后 N 行")
    other.add_argument("--json", action="store_true", help="--status 以 JSON 输出")
    other.add_argument("--adapter", help="临时覆盖适配器（generic / gdufe / mock_*）")
    other.add_argument("--verbose", action="store_true", help="输出调试日志")
    other.add_argument("--version", action="version", version=f"CampusLogin {APP_VERSION}")
    return parser


# ----------------------------------------------------------------------
def _prepare(args):
    config = load_config()
    if args.adapter:
        config.adapter = args.adapter
        config.normalize()
    if args.verbose:
        config.log_level = "DEBUG"
    setup_logging(config, console=False)
    store, _ = create_store(config.credential_backend)
    return config, store


def _state_to_text(state) -> str:
    lines = [f"认证状态：{state.describe()}"]
    if state.portal_url:
        lines.append(f"门户地址：{state.portal_url}")
    if state.reason:
        lines.append(f"说明：{state.reason}")
    return "\n".join(lines)


def _virtual_adapter_hint() -> list[str]:
    """返回当前在线的代理 / VPN 虚拟网卡描述（TUN 模式会影响校园网认证）。"""
    try:
        from .winiface import list_interfaces

        return [
            f"{item.name}({item.addresses[0]})"
            for item in list_interfaces()
            if item.is_virtual and item.up and item.addresses
        ]
    except Exception:
        return []


def cmd_status(args, config: Config, store) -> int:
    session = build_session(config)
    state = check_authentication(config, session=session)
    credential = store.load()
    autostart = AutostartManager(config)
    try:
        autostart_installed = autostart.is_installed()
    except Exception:
        autostart_installed = False
    credential_saved = bool(credential and credential.complete)

    if args.json:
        print(
            json.dumps(
                {
                    "authenticated": state.authenticated,
                    "needs_manual": state.needs_manual,
                    "network_down": state.network_down,
                    "portal_url": state.portal_url,
                    "reason": state.reason,
                    "adapter": config.adapter,
                    "config_path": str(config_path()),
                    "log_path": str(log_path()),
                    "credential_saved": credential_saved,
                    "credential_backend": store.name,
                    "autostart": autostart_installed,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(f"{APP_DISPLAY_NAME}")
        print("=" * 40)
        print(_state_to_text(state))
        print(f"适配器：{config.adapter}")
        print(
            f"凭据存储：{store.name}（{'已保存账号密码' if credential_saved else '尚未保存账号密码'}）"
        )
        print(f"开机自动运行：{'已开启' if autostart_installed else '未开启'}")
        virtual = _virtual_adapter_hint()
        if virtual:
            print(
                "⚠️ 检测到代理/VPN 虚拟网卡："
                + "、".join(virtual)
                + "（TUN 模式代理可能让认证请求被代理转发，建议认证期间关闭）"
            )
        print(f"配置文件：{config_path()}")
        print(f"日志文件：{log_path()}")

    if state.authenticated:
        return EXIT_OK
    if state.needs_manual:
        return EXIT_NEEDS_MANUAL
    return EXIT_NOT_ONLINE


def cmd_check(args, config: Config, store) -> int:
    session = build_session(config)
    state = check_authentication(config, session=session)
    print(_state_to_text(state))
    if state.authenticated:
        return EXIT_OK
    if state.needs_manual:
        return EXIT_NEEDS_MANUAL
    return EXIT_NOT_ONLINE


def cmd_login(args, config: Config, store) -> int:
    session = build_session(config)
    adapter = create_adapter(config, session=session)
    service = CampusLoginService(config, store, adapter)
    service.on_event = lambda event: print(
        f"[{STATUS_TEXT.get(event.status, event.status.value)}] {event.message}".strip()
    )
    status = service.ensure_online(ignore_block=True)
    if status in (Status.AUTHENTICATED, Status.ONLINE):
        if status is Status.ONLINE:
            print("认证成功：Portal 认证成功，互联网连通性检测通过")
        else:
            print("认证成功：Portal 认证成功（未配置互联网连通性检测地址，未做额外检测）")
        return EXIT_OK
    if adapter.portal_url:
        print(f"可手动打开登录页面：{adapter.portal_url}")
    if status is Status.NEEDS_MANUAL:
        print(f"需要人工处理：{service.message}")
        return EXIT_NEEDS_MANUAL
    print(f"认证失败：{service.message}")
    return EXIT_NOT_ONLINE


def cmd_install(args, config: Config) -> int:
    manager = AutostartManager(config)
    try:
        status = manager.install()
    except AutostartError as exc:
        print(f"设置开机自动运行失败：{exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    config.auto_start_on_boot = True
    save_config(config)
    print("已开启开机自动运行")
    print(status.describe())
    return EXIT_OK


def cmd_uninstall(args, config: Config) -> int:
    manager = AutostartManager(config)
    try:
        status = manager.uninstall()
    except AutostartError as exc:
        print(f"取消开机自动运行失败：{exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    config.auto_start_on_boot = False
    save_config(config)
    print("已取消开机自动运行")
    print(status.describe())
    return EXIT_OK


def cmd_config(args, config: Config, store) -> int:
    print(f"配置文件：{config_path()}")
    print(json.dumps(config.to_dict(), ensure_ascii=False, indent=2))
    credential = store.load()
    print(f"\n凭据存储方式：{store.name}")
    print(f"账号：{config.username or '(未设置)'}")
    print(
        "密码："
        + ("已保存（不显示，也不写入任何配置文件）" if credential and credential.password else "未保存")
    )
    return EXIT_OK


def cmd_tail(args, config: Config) -> int:
    path = log_path()
    if not path.exists():
        print(f"日志文件不存在：{path}")
        return EXIT_OK
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    count = max(1, int(args.tail or 50))
    for line in lines[-count:]:
        print(line)
    print(f"\n（日志文件：{path}）")
    return EXIT_OK


def cmd_quit() -> int:
    """让正在运行的托盘 / 后台程序退出（找不到实例也算成功）。"""
    from .tray import request_quit

    if request_quit():
        print("已通知正在运行的程序退出。")
        return EXIT_OK
    print("没有找到正在运行的程序（可能已经退出了）。")
    return EXIT_OK


# ----------------------------------------------------------------------
class _FormParser(HTMLParser):
    """极简表单解析器：找出 form / input / select 的名字，用于生成配置建议。"""

    def __init__(self) -> None:
        super().__init__()
        self.forms: list[dict] = []
        self._current: dict | None = None
        self.title_parts: list[str] = []
        self._in_title = False
        self.scripts = 0

    def handle_starttag(self, tag, attrs):
        data = {k.lower(): (v or "") for k, v in attrs}
        if tag == "form":
            self._current = {
                "action": data.get("action", ""),
                "method": (data.get("method") or "get").lower(),
                "id": data.get("id", ""),
                "inputs": [],
            }
            self.forms.append(self._current)
        elif tag == "input":
            item = {
                "name": data.get("name", ""),
                "type": (data.get("type") or "text").lower(),
                "id": data.get("id", ""),
                "value": data.get("value", ""),
            }
            if self._current is not None:
                self._current["inputs"].append(item)
            elif self.forms:
                self.forms[-1]["inputs"].append(item)
        elif tag == "select":
            item = {
                "name": data.get("name", ""),
                "type": "select",
                "id": data.get("id", ""),
                "value": "",
            }
            if self._current is not None:
                self._current["inputs"].append(item)
        elif tag == "title":
            self._in_title = True
        elif tag == "script":
            self.scripts += 1

    def handle_endtag(self, tag):
        if tag == "form":
            self._current = None
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title_parts.append(data.strip())


USERNAME_HINTS = ("username", "user", "account", "userid", "uid", "loginname", "name")
SERVICE_HINTS = ("service", "isp", "operator", "line", "type")
CAPTCHA_HINTS = ("验证码", "captcha", "checkcode", "vcode", "verifycode", "seccode", "短信验证")


def cmd_discover(args, config: Config) -> int:
    url = args.discover
    if not url.lower().startswith(("http://", "https://")):
        url = "http://" + url
    print(f"正在分析校园网登录页面：{url}")
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    try:
        response = session.get(url, timeout=(5, 15), allow_redirects=True)
    except Exception as exc:
        print(f"访问失败：{type(exc).__name__} {exc}", file=sys.stderr)
        print("提示：请确认当前网络需要认证，且该地址可以在浏览器中打开。")
        return EXIT_CONFIG_ERROR

    html = response.text or ""
    print(f"HTTP {response.status_code}，最终地址：{response.url}")
    parser = _FormParser()
    try:
        parser.feed(html)
    except Exception:
        pass
    title = "".join(parser.title_parts).strip()
    if title:
        print(f"页面标题：{title}")
    print(f"页面中发现 {len(parser.forms)} 个表单，{parser.scripts} 段脚本")

    # 只在表单元素自身（input / img 的 name、placeholder、src 等）里找，避免正文里的“验证码”误报
    captcha_hit = detect_captcha_field(html, CAPTCHA_HINTS)
    if captcha_hit:
        print(f"⚠️ 检测到验证码输入框（{captcha_hit}）：这种校园网无法自动完成认证，需要人工处理。")
    else:
        print("没有发现验证码输入框。")

    if not parser.forms:
        print("\n没有找到传统 HTML 表单：登录很可能是 JavaScript 提交（AJAX / JSON 接口）。")
        print("请用浏览器开发者工具抓取真实请求（见 README「如何获取校园网登录接口」）：")
        print("  F12 → Network → 输入账号密码 → 点击登录 → 找到 XHR/Fetch 请求 → 复制 URL 与参数名")
        return EXIT_OK

    suggestion: dict = {"adapter": "generic"}
    for index, form in enumerate(parser.forms, start=1):
        action = urljoin(str(response.url), form.get("action") or "")
        names = [i["name"] for i in form["inputs"] if i["name"]]
        print(f"\n表单 {index}：method={form['method'].upper()}  action={action or '(当前地址)'}")
        print(f"  字段：{', '.join(names) if names else '(无)'}")
        for item in form["inputs"]:
            if item["type"] == "password":
                print(f"    - {item['name'] or '(未命名)'} [密码字段，值：***]")
            elif item["type"] == "hidden":
                print(f"    - {item['name']} [隐藏字段，值：{item['value'][:40]}…]")

        password_items = [i for i in form["inputs"] if i["type"] == "password"]
        if password_items and index == 1:
            username_items = [
                i
                for i in form["inputs"]
                if i["type"] in ("text", "email", "tel")
                and any(hint in i["name"].lower() for hint in USERNAME_HINTS)
            ]
            service_items = [
                i
                for i in form["inputs"]
                if i["type"] in ("select", "radio")
                or any(hint in i["name"].lower() for hint in SERVICE_HINTS)
            ]
            hidden = {
                i["name"]: i["value"]
                for i in form["inputs"]
                if i["type"] == "hidden" and i["name"] and "token" not in i["name"].lower()
            }
            suggestion.update(
                {
                    "login_url": action,
                    "request_method": form["method"].upper(),
                    "username_field": (
                        username_items[0]["name"] if username_items else (names[0] if names else "")
                    ),
                    "password_field": password_items[0]["name"],
                    "content_type": "form",
                }
            )
            if service_items:
                suggestion["service_field"] = service_items[0]["name"]
            if hidden:
                suggestion["extra_fields"] = hidden

    suggestion.setdefault("login_url", str(response.url))
    print("\n根据页面表单推断的配置建议（请核对后再填入设置界面）：")
    print(json.dumps(suggestion, ensure_ascii=False, indent=2))
    print(
        "\n注意：\n"
        "  * 隐藏字段（token / 加密参数 / wlanuserip 等）可能是每次会话变化的；"
        "如果登录总是失败，请把 Network 面板里的请求参数（不含密码）发给我。\n"
        "  * 若页面由 JavaScript 加密密码或生成 Token，需要先看清加密方式再实现自动化。\n"
        "  * 不需要提供真实密码：密码在设置界面里输入，程序会存进 Windows 凭据管理器。"
    )
    return EXIT_OK


# ----------------------------------------------------------------------
def _print_result(name: str, ok: bool, detail: str = "") -> bool:
    flag = "通过" if ok else "未通过"
    print(f"[{flag}] {name}" + (f" — {detail}" if detail else ""))
    return ok


def _scenario_config(adapter: str) -> Config:
    config = Config()
    config.adapter = adapter
    config.max_retries = 3
    config.retry_schedule_seconds = [1, 1, 1]
    config.normalize()
    return config


def _portal_config(portal: LocalPortal, retries: bool = False) -> Config:
    config = Config()
    config.adapter = "generic"
    config.portal_url = portal.url("/portal")
    config.login_url = portal.url("/login")
    config.check_url = portal.url("/generate_204")
    config.check_timeout_seconds = 3
    config.request_timeout_seconds = 3
    if retries:
        config.max_retries = 3
        config.retry_schedule_seconds = [1, 1, 1]
    config.normalize()
    return config


def _eportal_config(portal: LocalPortal) -> Config:
    """ePortal 形态的测试配置（GET + JSONP 登录接口）。"""
    config = Config()
    config.adapter = "eportal"
    config.portal_url = portal.url("/portal")
    config.login_url = portal.url("/eportal/portal/login")
    config.check_url = portal.url("/generate_204")
    config.request_method = "GET"
    config.username_field = "username"      # 本机模拟门户使用的字段名
    config.password_field = "password"
    config.check_timeout_seconds = 3
    config.request_timeout_seconds = 3
    config.normalize()
    return config


def cmd_selftest(args, config: Config) -> int:
    """离线自检：模拟适配器 + 本机假门户，验证核心逻辑与真实 HTTP 代码路径。"""
    print(f"{APP_DISPLAY_NAME} 离线自检（不会访问真实校园网）")
    print("=" * 60)
    results: list[bool] = []
    credential = Credential("2021001", "test-password")

    def run_scenario(name: str, adapter_name: str, script, expect: Status, local_network: bool = True):
        scenario_config = _scenario_config(adapter_name)
        store = MemoryCredentialStore(credential)
        session = build_session(scenario_config)
        adapter = create_adapter(scenario_config, session=session)
        network = SimulatedNetwork(script, local_network=local_network)
        sleeper = FakeSleeper()
        service = CampusLoginService(
            scenario_config, store, adapter, network=network, sleeper=sleeper
        )
        status = service.ensure_online()
        ok = status is expect
        detail = f"状态={STATUS_TEXT.get(status, status.value)}，登录尝试={adapter.attempts} 次"
        if sleeper.waited:
            detail += f"，等待序列={sleeper.waited}"
        results.append(_print_result(name, ok, detail))
        return service, adapter

    run_scenario("已经认证 → 不重复登录", "mock_success", [online_state()], Status.ONLINE)
    run_scenario(
        "未认证 → 自动登录成功（认证服务器确认）",
        "mock_success",
        [login_required_state(), online_state()],
        Status.AUTHENTICATED,
    )
    run_scenario(
        "登录失败 → 按重试序列重试后停止",
        "mock_failure",
        [login_required_state()],
        Status.FAILED,
    )
    service, adapter = run_scenario(
        "需要验证码 → 停止自动尝试并提示人工处理",
        "mock_captcha",
        [captcha_state()],
        Status.NEEDS_MANUAL,
    )
    results.append(
        _print_result(
            "验证码场景不会提交密码（直接转人工处理）",
            adapter.attempts == 0,
            f"登录尝试={adapter.attempts} 次",
        )
    )
    run_scenario(
        "校园网不可访问 → 重试后暂停",
        "mock_offline",
        [network_down_state()],
        Status.FAILED,
    )
    run_scenario(
        "网卡未就绪 → 等待网络后失败退出",
        "mock_success",
        [login_required_state()],
        Status.FAILED,
        local_network=False,
    )

    # ---- 端到端：本机假校园网门户（走真实 HTTP 请求代码路径） ----
    print("-" * 60)
    with LocalPortal(username=credential.username, password=credential.password) as portal:
        e2e_config = _portal_config(portal)
        store = MemoryCredentialStore(credential)
        adapter = create_adapter(e2e_config, session=build_session(e2e_config))
        service = CampusLoginService(e2e_config, store, adapter, sleeper=FakeSleeper())
        status = service.ensure_online()
        results.append(
            _print_result(
                "端到端：本机模拟门户认证成功",
                status is Status.AUTHENTICATED,
                f"状态={STATUS_TEXT.get(status, status.value)}",
            )
        )

    with LocalPortal(
        username=credential.username, password=credential.password, redirect_on_success=True
    ) as portal:
        e2e_config = _portal_config(portal)
        store = MemoryCredentialStore(credential)
        adapter = create_adapter(e2e_config, session=build_session(e2e_config))
        service = CampusLoginService(e2e_config, store, adapter, sleeper=FakeSleeper())
        status = service.ensure_online()
        results.append(
            _print_result(
                "端到端：登录成功返回 302 时也能正确判定",
                status is Status.AUTHENTICATED,
                f"状态={STATUS_TEXT.get(status, status.value)}",
            )
        )

    with LocalPortal(username=credential.username, password=credential.password) as portal:
        e2e_config = _portal_config(portal, retries=True)
        store = MemoryCredentialStore(Credential(credential.username, "wrong-password"))
        adapter = create_adapter(e2e_config, session=build_session(e2e_config))
        service = CampusLoginService(e2e_config, store, adapter, sleeper=FakeSleeper())
        status = service.ensure_online()
        results.append(
            _print_result(
                "端到端：密码错误时不做无限快速重试",
                status is Status.FAILED and adapter.attempts == 1,
                f"状态={STATUS_TEXT.get(status, status.value)}，登录尝试={adapter.attempts} 次",
            )
        )

    with LocalPortal(captcha=True) as portal:
        e2e_config = _portal_config(portal)
        store = MemoryCredentialStore(credential)
        adapter = create_adapter(e2e_config, session=build_session(e2e_config))
        service = CampusLoginService(e2e_config, store, adapter, sleeper=FakeSleeper())
        status = service.ensure_online()
        results.append(
            _print_result(
                "端到端：需要验证码时转人工处理",
                status is Status.NEEDS_MANUAL,
                f"状态={STATUS_TEXT.get(status, status.value)}",
            )
        )

    # ---- ePortal（深澜/城市热点）JSONP 接口：result=1 就是认证成功 ----
    with LocalPortal(
        username=credential.username,
        password=credential.password,
        eportal=True,
        probe_still_hijacked=True,
    ) as portal:
        eportal_config = _eportal_config(portal)
        store = MemoryCredentialStore(credential)
        adapter = create_adapter(eportal_config, session=build_session(eportal_config))
        service = CampusLoginService(eportal_config, store, adapter, sleeper=FakeSleeper())
        status = service.ensure_online()
        results.append(
            _print_result(
                "端到端：ePortal 返回 result=1，即使状态探测仍被重定向也判定认证成功",
                status is Status.AUTHENTICATED,
                f"状态={STATUS_TEXT.get(status, status.value)}",
            )
        )

        eportal_config.internet_check_url = portal.url("/generate_204")
        adapter = create_adapter(eportal_config, session=build_session(eportal_config))
        service = CampusLoginService(eportal_config, store, adapter, sleeper=FakeSleeper())
        status = service.ensure_online()
        results.append(
            _print_result(
                "端到端：互联网连通性检测失败不改变“认证成功”状态",
                status is Status.AUTHENTICATED,
                f"状态={STATUS_TEXT.get(status, status.value)}",
            )
        )

    with LocalPortal(username=credential.username, password=credential.password, eportal=True) as portal:
        eportal_config = _eportal_config(portal)
        eportal_config.internet_check_url = portal.url("/generate_204")
        store = MemoryCredentialStore(credential)
        adapter = create_adapter(eportal_config, session=build_session(eportal_config))
        service = CampusLoginService(eportal_config, store, adapter, sleeper=FakeSleeper())
        status = service.ensure_online()
        results.append(
            _print_result(
                "端到端：Portal 认证成功 + 互联网连通性检测通过 → 已连接互联网",
                status is Status.ONLINE,
                f"状态={STATUS_TEXT.get(status, status.value)}",
            )
        )

    print("=" * 60)
    passed = sum(1 for item in results if item)
    print(f"自检结果：{passed}/{len(results)} 项通过")
    return EXIT_OK if passed == len(results) else EXIT_NOT_ONLINE


# ----------------------------------------------------------------------
def _run_tray() -> int:
    guard = SingleInstance()
    if not guard.acquire():
        message_box(APP_DISPLAY_NAME, "校园网自动登录已经在运行（请看任务栏右下角的托盘图标）。")
        return EXIT_OK
    try:
        app = Application()
        return app.run_tray()
    finally:
        guard.release()


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.settings:
        from .settings_gui import main as settings_main

        return settings_main()

    if args.selftest:
        setup_logging(Config(), console=False)
        return cmd_selftest(args, Config())

    # --quit 不需要读配置 / 初始化日志，保证在任何情况下都能让后台程序退出
    if args.quit:
        return cmd_quit()

    config, store = _prepare(args)

    if args.status:
        return cmd_status(args, config, store)
    if args.check:
        return cmd_check(args, config, store)
    if args.login:
        return cmd_login(args, config, store)
    if args.discover:
        return cmd_discover(args, config)
    if args.install:
        return cmd_install(args, config)
    if args.uninstall:
        return cmd_uninstall(args, config)
    if args.config:
        return cmd_config(args, config, store)
    if args.log_path:
        print(log_path())
        return EXIT_OK
    if args.tail is not None:
        return cmd_tail(args, config)
    if args.credential:
        credential = ensure_credential_interactive(config, store)
        return EXIT_OK if credential else EXIT_CONFIG_ERROR
    if args.daemon:
        return Application(config).run_daemon()

    # 默认动作：托盘后台运行（--tray 与不带参数等价）
    return _run_tray()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
