"""“首次询问开启自启”和“程序位置变化自愈”的测试。

背景：同学从 Release 下载 zip、双击 exe 用起来之后，如果没人告诉他/她勾选
“开机自动运行”，重启电脑就不会自动启动（日志里也不会有任何记录）。
所以程序现在会：① 首次运行时弹一次“要不要开机自动运行”；② 发现程序被移动过
就自动把启动项更新到新位置。
"""

from __future__ import annotations

import unittest

from campus_login.app import Application
from campus_login.autostart import commands_match, split_command_line
from campus_login.config import Config, load_config

from .support import TempDataDirTestCase

SOURCE_COMMAND = r'"C:\Python\pythonw.exe" "C:\App\campus_login_main.py" --tray'


class CommandsMatchTest(unittest.TestCase):
    def test_same_command(self):
        self.assertTrue(commands_match(SOURCE_COMMAND, split_command_line(SOURCE_COMMAND)))

    def test_case_insensitive(self):
        self.assertTrue(commands_match(SOURCE_COMMAND.upper(), split_command_line(SOURCE_COMMAND)))

    def test_script_path_changed(self):
        moved = r'"C:\Python\pythonw.exe" "C:\Desktop\CampusLogin\campus_login_main.py" --tray'
        self.assertFalse(commands_match(moved, split_command_line(SOURCE_COMMAND)))

    def test_exe_moved(self):
        recorded = r"C:\Downloads\CampusLogin\dist\CampusLogin\CampusLogin.exe --tray"
        moved = [r"C:\Desktop\CampusLogin\dist\CampusLogin\CampusLogin.exe", "--tray"]
        same = [r"C:\Downloads\CampusLogin\dist\CampusLogin\CampusLogin.exe", "--tray"]
        self.assertFalse(commands_match(recorded, moved))
        self.assertTrue(commands_match(recorded, same))

    def test_empty_recorded(self):
        self.assertFalse(commands_match("", ["a.exe", "--tray"]))


class FakeAutostart:
    """假的 AutostartManager：可控 is_installed / current_command / install。"""

    class Backend:
        name = "task"
        location = "假任务"

        def __init__(self, command: str) -> None:
            self._command = command

        def current_command(self) -> str:
            return self._command

    def __init__(self, installed: bool = False, recorded: str = "") -> None:
        self.installed = installed
        self.recorded = recorded
        self.install_calls = 0
        self.backend = self.Backend(recorded)

    def is_installed(self) -> bool:
        return self.installed

    def active_backend(self):
        return self.backend

    def install(self):
        self.install_calls += 1
        self.installed = True
        return object()


class FirstRunPromptTest(TempDataDirTestCase):
    def build(self, installed: bool = False, prompted: bool = False, auto_start: bool = False):
        config = Config()
        config.autostart_prompted = prompted
        config.auto_start_on_boot = auto_start
        config.normalize()
        app = Application(config)
        fake = FakeAutostart(installed=installed)
        app.autostart = fake
        import campus_login.app as app_module

        self.answers: list[bool] = []
        app_module.ask_yes_no = lambda *a, **k: self.answers.pop(0) if self.answers else False
        self.addCleanup(lambda: setattr(app_module, "ask_yes_no", app_module.ask_yes_no))
        return app, fake

    def test_asks_on_first_manual_run(self):
        app, fake = self.build()
        self.answers.append(True)
        self.assertTrue(app._should_ask_autostart())
        self.assertTrue(app.maybe_ask_autostart())
        self.assertEqual(fake.install_calls, 1)
        self.assertTrue(app.config.auto_start_on_boot)
        self.assertTrue(app.config.autostart_prompted)

    def test_remembers_no_answer(self):
        app, fake = self.build()
        self.answers.append(False)
        self.assertFalse(app.maybe_ask_autostart())
        self.assertEqual(fake.install_calls, 0)
        self.assertTrue(app.config.autostart_prompted)
        # 第二次启动不再问
        self.assertFalse(app._should_ask_autostart())

    def test_does_not_ask_when_already_installed(self):
        app, fake = self.build(installed=True)
        self.assertFalse(app._should_ask_autostart())
        self.assertFalse(app.maybe_ask_autostart())
        self.assertEqual(fake.install_calls, 0)

    def test_does_not_ask_when_already_prompted(self):
        app, _ = self.build(prompted=True)
        self.assertFalse(app._should_ask_autostart())

    def test_answer_is_saved_to_config(self):
        app, _ = self.build()
        self.answers.append(True)
        app.maybe_ask_autostart()
        self.assertTrue(load_config().autostart_prompted)


class AutostartPathSyncTest(TempDataDirTestCase):
    def build(self, auto_start: bool, installed: bool, recorded: str):
        config = Config()
        config.auto_start_on_boot = auto_start
        config.normalize()
        app = Application(config)
        fake = FakeAutostart(installed=installed, recorded=recorded)
        app.autostart = fake
        return app, fake

    def test_updates_when_program_was_moved(self):
        moved = r'"C:\Python\pythonw.exe" "C:\Old\Place\campus_login_main.py" --tray'
        app, fake = self.build(auto_start=True, installed=True, recorded=moved)
        self.assertTrue(app.sync_autostart_path())
        self.assertEqual(fake.install_calls, 1)

    def test_no_action_when_path_unchanged(self):
        from campus_login.paths import run_command

        app, fake = self.build(auto_start=True, installed=True, recorded=run_command(("--tray",)))
        self.assertFalse(app.sync_autostart_path())
        self.assertEqual(fake.install_calls, 0)

    def test_no_action_when_not_installed(self):
        app, fake = self.build(auto_start=True, installed=False, recorded="")
        self.assertFalse(app.sync_autostart_path())
        self.assertEqual(fake.install_calls, 0)

    def test_no_action_when_autostart_disabled(self):
        app, fake = self.build(auto_start=False, installed=True, recorded="whatever")
        self.assertFalse(app.sync_autostart_path())
        self.assertEqual(fake.install_calls, 0)

    def test_no_action_when_command_unreadable(self):
        app, fake = self.build(auto_start=True, installed=True, recorded="")
        self.assertFalse(app.sync_autostart_path())
        self.assertEqual(fake.install_calls, 0)


if __name__ == "__main__":
    unittest.main()
