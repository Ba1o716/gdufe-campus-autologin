"""把配置、凭据、服务、托盘组装成可运行的应用程序。"""

from __future__ import annotations

import ctypes
import logging
import os
import queue
import subprocess
import sys
import threading
import webbrowser

from .adapters import BaseAdapter, create_adapter
from .autostart import AutostartError, AutostartManager
from .config import Config, load_config, save_config
from .credentials import Credential, create_store, save_credential
from .logging_setup import get_logger, redactor, setup_logging
from .network import build_session
from .paths import config_path, launch_args, log_path
from .service import CampusLoginService, ServiceEvent, Status, STATUS_TEXT
from .tray import (
    CMD_AUTOSTART,
    CMD_CHECK,
    CMD_EXIT,
    CMD_LOGIN,
    CMD_OPEN_PORTAL,
    CMD_SETTINGS,
    CMD_STATUS,
    CMD_VIEW_LOG,
    MenuItem,
    TrayIcon,
    hide_console_window,
)

CMD_SEPARATOR = 0


def message_box(title: str, text: str, error: bool = False, timeout_ms: int = 15000) -> None:
    """Windows 原生提示框。

    使用带超时的 MessageBoxTimeoutW：即使没人在电脑前（例如开机自动启动时），
    提示框也会自动消失，不会把程序卡住。
    """
    if os.name != "nt":
        print(f"{title}: {text}")
        return
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        flags = 0x00000000 | 0x00010000 | (0x10 if error else 0x40)  # OK | SETFOREGROUND | ICON
        try:
            timeout_box = user32.MessageBoxTimeoutW
            timeout_box.argtypes = [
                ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p,
                ctypes.c_uint, ctypes.c_ushort, ctypes.c_uint,
            ]
            timeout_box.restype = ctypes.c_int
            timeout_box(None, text, title, flags, 0, int(timeout_ms))
            return
        except AttributeError:
            pass
        user32.MessageBoxW.argtypes = [
            ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint
        ]
        user32.MessageBoxW(None, text, title, flags)
    except Exception:
        print(f"{title}: {text}")


class CommandSleeper:
    """可被托盘命令打断的等待：既实现重试间隔，又能即时响应“立即检查 / 退出”。"""

    def __init__(self, app: "Application") -> None:
        self.app = app
        self.event = threading.Event()
        self.waited: list[float] = []

    def sleep(self, seconds: float) -> bool:
        seconds = max(0.0, float(seconds))
        self.waited.append(seconds)
        if self.event.is_set():
            return False
        deadline = _monotonic() + seconds
        while True:
            remaining = deadline - _monotonic()
            if remaining <= 0:
                return not self.event.is_set()
            try:
                command = self.app.commands.get(timeout=min(remaining, 0.5))
            except queue.Empty:
                if self.event.is_set():
                    return False
                continue
            self.app.handle_command(command)
            if command == "exit":
                return False
            # 其它命令：立即结束等待，让主循环尽快重新检查/登录
            return not self.event.is_set()

    def stop(self) -> None:
        self.event.set()

    @property
    def stopped(self) -> bool:
        return self.event.is_set()


def _monotonic() -> float:
    import time

    return time.monotonic()


class Application:
    """托盘模式的完整应用。"""

    def __init__(self, config: Config | None = None) -> None:
        self.config = config or load_config()
        self.log_file = setup_logging(self.config)
        self.log = get_logger("app")
        self.store, self.store_description = create_store(self.config.credential_backend, self.log)
        self.session = build_session(self.config)
        self.adapter: BaseAdapter = create_adapter(self.config, logger=self.log, session=self.session)
        self.commands: queue.Queue[str] = queue.Queue()
        self.sleeper = CommandSleeper(self)
        self.service = CampusLoginService(
            self.config,
            self.store,
            self.adapter,
            sleeper=self.sleeper,
            on_event=self._on_event,
        )
        self.autostart = AutostartManager(self.config, logger=self.log)
        self.tray: TrayIcon | None = None
        self._thread: threading.Thread | None = None
        self._last_important: str = ""
        self._config_mtime: float = 0.0
        self._remember_config_mtime()

    # ------------------------------------------------------------------
    def _on_event(self, event: ServiceEvent) -> None:
        text = f"{STATUS_TEXT.get(event.status, event.status.value)}"
        if event.message:
            text = f"{text}：{event.message}"
        if self.tray:
            self.tray.set_tooltip(f"校园网自动登录 - {text}")
        if event.important and event.status in (Status.NEEDS_MANUAL, Status.FAILED):
            if event.message != self._last_important:
                self._last_important = event.message
                title = "需要人工处理" if event.status is Status.NEEDS_MANUAL else "校园网认证失败"
                if self.tray:
                    from .tray import NIIF_ERROR, NIIF_WARNING

                    level = NIIF_WARNING if event.status is Status.NEEDS_MANUAL else NIIF_ERROR
                    self.tray.notify(title, event.message, level)

    # ------------------------------------------------------------------
    def _remember_config_mtime(self) -> None:
        try:
            self._config_mtime = config_path().stat().st_mtime
        except OSError:
            self._config_mtime = 0.0

    def reload_config_if_changed(self) -> bool:
        """设置界面保存后，托盘进程无需重启即可生效。"""
        try:
            mtime = config_path().stat().st_mtime
        except OSError:
            return False
        if mtime == self._config_mtime:
            return False
        self._config_mtime = mtime
        try:
            new_config = load_config()
        except Exception as exc:
            self.log.warning("重新加载配置失败：%s", type(exc).__name__)
            return False
        self.config = new_config
        self.session = build_session(new_config)
        self.adapter = create_adapter(new_config, logger=self.log, session=self.session)
        self.service.config = new_config
        self.service.adapter = self.adapter
        self.autostart = AutostartManager(new_config, logger=self.log)
        self.service.reset_block()
        self.log.info("检测到配置更新，已重新加载")
        if self.tray:
            self.tray.set_tooltip("校园网自动登录 - 配置已更新")
        return True

    # ------------------------------------------------------------------
    def _supervise(self) -> None:
        """后台线程：完成认证，然后低频巡检。"""
        try:
            self.log.info("后台服务线程启动")
            if self.config.startup_delay_seconds > 0:
                self.log.info("启动延迟 %.0f 秒（等待系统网络就绪）", self.config.startup_delay_seconds)
                if not self.sleeper.sleep(self.config.startup_delay_seconds):
                    return
            while not self.sleeper.stopped:
                self.reload_config_if_changed()
                self.service.ensure_online()
                if self.sleeper.stopped:
                    break
                if not self.sleeper.sleep(self.config.check_interval_seconds):
                    break
        except Exception as exc:
            self.log.error("后台服务线程异常退出：%s", type(exc).__name__)
        finally:
            self.log.info("后台服务线程结束")

    def start_service(self) -> None:
        self.service.reset_block()
        self._thread = threading.Thread(target=self._supervise, name="campus-login-service", daemon=True)
        self._thread.start()

    def stop_service(self) -> None:
        self.sleeper.stop()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)

    # ------------------------------------------------------------------
    # 托盘菜单
    # ------------------------------------------------------------------
    def menu_items(self) -> list[MenuItem]:
        status = self.service.status
        text = STATUS_TEXT.get(status, status.value)
        if self.service.message and status in (Status.FAILED, Status.NEEDS_MANUAL):
            hint = self.service.message
            if len(hint) > 40:
                hint = hint[:40] + "…"
            text = f"{text}（{hint}）"
        autostart_on = False
        try:
            autostart_on = self.autostart.is_installed()
        except Exception:
            autostart_on = bool(self.config.auto_start_on_boot)
        return [
            MenuItem(CMD_STATUS, "校园网自动登录", enabled=False),
            MenuItem(CMD_STATUS, f"● {text}", enabled=False),
            MenuItem(CMD_SEPARATOR, "", separator=True),
            MenuItem(CMD_CHECK, "立即检查"),
            MenuItem(CMD_LOGIN, "立即登录", enabled=status is not Status.LOGGING_IN),
            MenuItem(CMD_OPEN_PORTAL, "打开登录页面"),
            MenuItem(CMD_VIEW_LOG, "查看日志"),
            MenuItem(CMD_SETTINGS, "设置"),
            MenuItem(CMD_AUTOSTART, "开机自动运行", checked=autostart_on),
            MenuItem(CMD_SEPARATOR, "", separator=True),
            MenuItem(CMD_EXIT, "退出"),
        ]

    def on_tray_command(self, command: int) -> None:
        """托盘回调（UI 线程）。耗时操作交给后台线程。"""
        if command == CMD_OPEN_PORTAL:
            self.open_portal()
            return
        if command == CMD_VIEW_LOG:
            self.open_log()
            return
        if command == CMD_SETTINGS:
            self.open_settings()
            return
        if command == CMD_EXIT:
            if self.tray:
                self.tray.stop()
            self.stop_service()
            return
        if command == CMD_CHECK:
            if self.tray:
                self.tray.set_tooltip("校园网自动登录 - 正在检查认证状态")
            self.commands.put("check")
            return
        if command == CMD_LOGIN:
            if self.tray:
                self.tray.set_tooltip("校园网自动登录 - 正在登录")
            self.commands.put("login")
            return
        if command == CMD_AUTOSTART:
            self.commands.put("autostart")
            return

    def handle_command(self, command: str) -> None:
        """在后台线程处理托盘命令。"""
        if command == "exit":
            self.log.info("用户选择退出")
            self.sleeper.stop()
            return
        if command == "login":
            self.log.info("用户手动触发登录")
            self.service.reset_block()
            return
        if command == "check":
            self.log.info("用户手动触发认证状态检查")
            return
        if command == "autostart":
            self.toggle_autostart()
            return

    # ------------------------------------------------------------------
    # 独立动作
    # ------------------------------------------------------------------
    def open_portal(self) -> None:
        url = self.adapter.portal_url
        if not url:
            message_box(
                "尚未配置校园网登录页面",
                "请先打开“设置”填写校园网登录页面地址（portal_url），\n"
                "或者运行 --discover <登录页面地址> 分析接口参数。",
            )
            return
        try:
            webbrowser.open(url)
            self.log.info("已在浏览器中打开登录页面")
        except Exception as exc:
            self.log.warning("打开登录页面失败：%s", type(exc).__name__)
            message_box("打开登录页面失败", f"请手动访问：{url}", error=True)

    def open_log(self) -> None:
        path = self.log_file or log_path()
        try:
            os.startfile(str(path))  # noqa: S606  (Windows 专用)
        except Exception:
            message_box("日志文件位置", str(path))

    def open_settings(self) -> None:
        """在独立进程里打开设置界面（避免 tkinter 与托盘消息循环互相干扰）。"""
        try:
            subprocess.Popen(launch_args(("--settings",)))
        except Exception as exc:
            self.log.error("打开设置界面失败：%s", type(exc).__name__)
            message_box("打开设置界面失败", f"请手动运行：--settings\n{exc}", error=True)

    def toggle_autostart(self) -> None:
        try:
            installed = self.autostart.is_installed()
            if installed:
                result = self.autostart.uninstall()
                self.config.auto_start_on_boot = False
                message = "已关闭开机自动运行"
            else:
                result = self.autostart.install()
                self.config.auto_start_on_boot = True
                message = "已开启开机自动运行"
            save_config(self.config)
            self.log.info("%s（%s）", message, result.location)
        except AutostartError as exc:
            self.log.error("设置开机自动运行失败：%s", exc)
            message_box("设置开机自动运行失败", str(exc), error=True)

    # ------------------------------------------------------------------
    def run_tray(self) -> int:
        """托盘模式。若当前环境无法显示托盘图标，则退化为“后台运行”，仍然会自动登录。"""
        hide_console_window()
        if sys.platform != "win32":
            self.log.error("系统托盘仅支持 Windows")
            return 1
        try:
            self.tray = TrayIcon(
                title="校园网自动登录",
                menu_provider=self.menu_items,
                on_command=self.on_tray_command,
            )
            self.start_service()
            self.tray.run()
        except Exception as exc:
            self.log.error("系统托盘启动失败：%s", exc)
            self.log.warning(
                "程序继续在后台自动登录校园网（不显示托盘图标）；"
                "可用 --status 查看状态、--quit 关闭，日志见 %s",
                self.log_file,
            )
            if self._thread is None:
                self.start_service()
            # 即使没有托盘图标，也保持一个消息循环，这样 --quit 依然能关掉本进程
            try:
                if self.tray and self.tray.hwnd:
                    self.tray.pump_until_close()
                else:
                    while self._thread and self._thread.is_alive():
                        self._thread.join(timeout=1.0)
            except KeyboardInterrupt:
                self.service.stop()
        finally:
            self.stop_service()
        return 0

    def run_daemon(self) -> int:
        """前台运行（控制台输出，Ctrl+C 退出）。"""
        self.log.info("以前台模式运行（Ctrl+C 退出）")
        try:
            self.service.run_forever()
        except KeyboardInterrupt:
            self.log.info("收到中断信号，退出")
            self.service.stop()
        return 0


def ensure_credential_interactive(config: Config, store) -> Credential | None:
    """在控制台里安全地输入账号密码（不回显、不写日志）。"""
    import getpass

    existing = store.load() if store else None
    default_user = (existing.username if existing else "") or config.username
    prompt = f"校园网账号[{default_user}]: " if default_user else "校园网账号: "
    try:
        username = input(prompt).strip() or default_user
        password = getpass.getpass("校园网密码（输入时不显示）: ")
    except (EOFError, KeyboardInterrupt):
        print("\n没有读取到输入，已取消。请在交互式命令行中运行本命令，或使用 --settings 打开设置界面。")
        return None
    if not username or not password:
        print("账号或密码为空，已取消。")
        return None
    credential = Credential(username, password)
    store_used = save_credential(store, credential)
    redactor().add_secret(password)
    config.username = username
    save_config(config)
    print(f"已安全保存账号密码（存储方式：{getattr(store_used, 'name', 'unknown')} → {_store_hint(store_used)}）")
    print(f"配置文件：{config_path()}")
    return credential


def _store_hint(store) -> str:
    name = getattr(store, "name", "")
    if name == "credman":
        return "Windows 凭据管理器"
    if name == "dpapi":
        return "DPAPI 加密文件"
    return "内存（重启后失效）"
