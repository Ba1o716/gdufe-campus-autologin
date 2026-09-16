"""系统托盘与应用组装测试。"""

from __future__ import annotations

import ctypes
import os
import unittest

from campus_login.app import Application
from campus_login.tray import (
    CMD_AUTOSTART,
    CMD_CHECK,
    CMD_EXIT,
    CMD_LOGIN,
    CMD_OPEN_PORTAL,
    CMD_SETTINGS,
    CMD_VIEW_LOG,
    NOTIFYICONDATAW,
    WM_APP_STATUS,
    WM_COMMAND,
    WM_LBUTTONDBLCLK,
    WM_TRAYICON,
    TrayIcon,
    request_quit,
    running_window_handle,
    _default_menu,
    hide_console_window,
)
from campus_login.service import ServiceEvent, Status

from .support import TempDataDirTestCase

IS_WINDOWS = os.name == "nt"
IS_64BIT = ctypes.sizeof(ctypes.c_void_p) == 8


class TrayStructureTest(unittest.TestCase):
    @unittest.skipUnless(IS_WINDOWS and IS_64BIT, "仅在 64 位 Windows 上校验结构体大小")
    def test_notify_icon_data_size(self):
        # 必须与 Windows 头文件里的 sizeof(NOTIFYICONDATAW) 一致，否则 Shell_NotifyIcon 会失败
        self.assertEqual(ctypes.sizeof(NOTIFYICONDATAW), 976)

    def test_default_menu_contains_exit(self):
        menu = _default_menu()
        self.assertTrue(any(item.id == CMD_EXIT for item in menu))

    @unittest.skipUnless(IS_WINDOWS, "Windows 专用")
    def test_hide_console_window_does_not_raise(self):
        hide_console_window()

    def test_command_ids_are_unique(self):
        ids = [
            CMD_CHECK,
            CMD_LOGIN,
            CMD_OPEN_PORTAL,
            CMD_VIEW_LOG,
            CMD_SETTINGS,
            CMD_AUTOSTART,
            CMD_EXIT,
        ]
        self.assertEqual(len(ids), len(set(ids)))


@unittest.skipUnless(IS_WINDOWS, "Windows 专用")
class TrayWindowTest(TempDataDirTestCase):
    """真实创建窗口并投递消息，验证托盘回调逻辑（不需要任务栏）。"""

    def build_tray(self):
        commands: list[int] = []
        tray = TrayIcon(title="CampusLogin 测试", on_command=commands.append)
        tray._libs()
        self.assertTrue(tray._create_window(), "应当能创建托盘消息窗口")
        self.addCleanup(lambda: tray._user32.DestroyWindow(tray.hwnd))
        return tray, commands

    def test_double_click_triggers_immediate_check(self):
        tray, commands = self.build_tray()
        tray._on_message(tray.hwnd, WM_TRAYICON, 0, WM_LBUTTONDBLCLK)
        self.assertEqual(commands, [CMD_CHECK])

    def test_menu_command_is_dispatched(self):
        tray, commands = self.build_tray()
        tray._on_message(tray.hwnd, WM_COMMAND, CMD_LOGIN, 0)
        self.assertEqual(commands, [CMD_LOGIN])

    def test_status_message_does_not_raise(self):
        tray, _ = self.build_tray()
        tray.set_tooltip("已认证")
        tray._on_message(tray.hwnd, WM_APP_STATUS, 0, 0)
        self.assertEqual(tray._tooltip, "已认证")

    def test_unknown_message_falls_back_to_default_proc(self):
        tray, commands = self.build_tray()
        result = tray._on_message(tray.hwnd, 0x0123, 0, 0)
        self.assertEqual(commands, [])
        self.assertIsNotNone(result)

    def test_stop_before_running_does_not_raise(self):
        tray = TrayIcon(title="CampusLogin 测试")
        tray.stop()


class ApplicationMenuTest(TempDataDirTestCase):
    def test_menu_items_match_specification(self):
        app = Application()
        items = app.menu_items()
        labels = [item.label for item in items]
        self.assertTrue(any("校园网自动登录" in label for label in labels))
        self.assertIn("立即检查", labels)
        self.assertIn("立即登录", labels)
        self.assertIn("打开登录页面", labels)
        self.assertIn("查看日志", labels)
        self.assertIn("设置", labels)
        self.assertIn("退出", labels)
        self.assertTrue(any("开机自动运行" in label for label in labels))
        self.assertTrue(any(item.separator for item in items))
        self.assertTrue(any("●" in label for label in labels))

    def test_status_line_is_chinese(self):
        app = Application()
        status_item = [item for item in app.menu_items() if "●" in item.label][0]
        self.assertIn("空闲", status_item.label)

    def test_service_events_do_not_require_a_tray(self):
        app = Application()
        app.tray = None
        app._on_event(ServiceEvent(Status.ONLINE, "校园网已连接/认证成功"))
        app._on_event(ServiceEvent(Status.NEEDS_MANUAL, "需要人工验证", important=True))
        app._on_event(ServiceEvent(Status.FAILED, "认证失败", important=True))

    def test_unknown_tray_command_is_ignored(self):
        app = Application()
        app.on_tray_command(99999)

    def test_configuration_is_reloaded_after_settings_save(self):
        from campus_login.config import Config, save_config

        app = Application()
        self.assertFalse(app.reload_config_if_changed())
        config = Config()
        config.adapter = "mock_success"
        config.portal_url = "http://10.0.0.1/portal"
        save_config(config)
        self.assertTrue(app.reload_config_if_changed())
        self.assertEqual(app.config.adapter, "mock_success")
        self.assertIn("/portal", app.adapter.portal_url)


@unittest.skipUnless(IS_WINDOWS, "Windows 专用")
class ServiceThreadTest(TempDataDirTestCase):
    def test_background_thread_completes_authentication_and_stops(self):
        import time

        from campus_login import network as network_module
        from campus_login.network import AuthState

        original_check = network_module.check_authentication
        original_wait = network_module.wait_for_local_network
        network_module.check_authentication = lambda *a, **k: AuthState(authenticated=True)
        network_module.wait_for_local_network = lambda *a, **k: True
        try:
            app = Application()
            app.config.startup_delay_seconds = 0
            app.config.check_interval_seconds = 5
            app.start_service()
            deadline = time.time() + 5
            while time.time() < deadline and app.service.status is not Status.ONLINE:
                time.sleep(0.05)
            self.assertIs(app.service.status, Status.ONLINE)
            app.stop_service()
            self.assertTrue(app.sleeper.stopped)
        finally:
            network_module.check_authentication = original_check
            network_module.wait_for_local_network = original_wait


@unittest.skipUnless(
    os.environ.get("CAMPUSLOGIN_TEST_TRAY") == "1",
    "默认不创建真实托盘图标（设置 CAMPUSLOGIN_TEST_TRAY=1 可测试）",
)
class LiveTrayTest(TempDataDirTestCase):
    """真实调用 Shell_NotifyIcon（任务栏会一瞬间出现托盘图标）。"""

    def test_icon_can_be_added_and_removed(self):
        tray = TrayIcon(title="CampusLogin 自检")
        tray._libs()
        self.assertTrue(tray._create_window())
        try:
            self.assertTrue(tray._add_icon(), "Shell_NotifyIcon(NIM_ADD) 应当成功")
            tray.set_tooltip("CampusLogin 自检 - 已认证")
            tray.notify("校园网自动登录", "托盘自检通知")
        finally:
            tray._remove_icon()
            tray._user32.DestroyWindow(tray.hwnd)


@unittest.skipUnless(IS_WINDOWS, "Windows 专用")
class TrayExitTest(TempDataDirTestCase):
    """回归测试：点“退出”必须真的退出（历史上这里会陷入 WM_CLOSE 死循环）。

    没有任务栏时 Shell_NotifyIcon 会失败，所以这里把图标的添加/删除替换掉，
    只验证“窗口 + 消息循环 + 退出”这条路径。
    """

    def start_loop(self, tray: TrayIcon):
        import threading
        import time

        tray._add_icon = lambda: True          # type: ignore[assignment]
        tray._remove_icon = lambda: None       # type: ignore[assignment]
        finished = threading.Event()

        def loop():
            try:
                tray.run()
            finally:
                finished.set()

        thread = threading.Thread(target=loop, daemon=True)
        thread.start()
        deadline = time.time() + 5
        while time.time() < deadline and not tray.hwnd:
            time.sleep(0.05)
        self.assertIsNotNone(tray.hwnd, "托盘窗口应当创建成功")
        return finished

    def test_stop_terminates_message_loop(self):
        tray = TrayIcon(title="退出测试")
        finished = self.start_loop(tray)
        tray.stop()
        self.assertTrue(finished.wait(5.0), "调用 stop() 之后消息循环必须退出（不能死循环）")

    def test_quit_command_finds_running_instance(self):
        tray = TrayIcon(title="退出命令测试")
        finished = self.start_loop(tray)
        self.assertIsNotNone(running_window_handle(), "应当能按窗口标题找到运行中的实例")
        self.assertTrue(request_quit(), "--quit 应当能通知运行中的程序退出")
        self.assertTrue(finished.wait(5.0), "--quit 之后消息循环必须退出")
