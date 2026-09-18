"""开机自动运行（任务计划程序 / 注册表）测试。"""

from __future__ import annotations

import os
import unittest

from campus_login.autostart import (
    AutostartError,
    AutostartManager,
    RegistryBackend,
    TaskSchedulerBackend,
)
from campus_login.config import Config
from campus_login.paths import run_command

from .support import TempDataDirTestCase

COMMAND = '"C:\\Python\\pythonw.exe" "C:\\App\\campus_login_main.py" --tray'


class FakeSchtasks:
    """模拟 schtasks.exe，用于在不改动系统的前提下测试安装/卸载逻辑。"""

    def __init__(self, fail: bool = False) -> None:
        self.tasks: dict[str, str] = {}
        self.calls: list[list[str]] = []
        self.fail = fail
        self.xml_seen = ""

    def __call__(self, args):
        args = [str(item) for item in args]
        self.calls.append(args)
        if self.fail:
            return 1, "错误: 拒绝访问。"
        verb = args[1].lower()
        if verb == "/create":
            name = args[args.index("/TN") + 1]
            if "/XML" in args:
                from pathlib import Path
                from xml.etree import ElementTree

                xml_text = Path(args[args.index("/XML") + 1]).read_text(encoding="utf-16")
                self.xml_seen = xml_text
                # 模仿 schtasks /Query /V 的输出：把 XML 里的命令拼回一行
                ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
                node = ElementTree.fromstring(xml_text).find("t:Actions/t:Exec", ns)
                self.tasks[name] = (
                    f"{node.find('t:Command', ns).text} {node.find('t:Arguments', ns).text}".strip()
                )
                return 0, f'成功: 已创建计划任务 "{name}"。'
            command = args[args.index("/TR") + 1]
            self.tasks[name] = command
            return 0, f'成功: 已创建计划任务 "{name}"。'
        if verb == "/query":
            name = args[args.index("/TN") + 1]
            if name in self.tasks:
                return 0, f"任务名: {name}\r\n要运行的任务: {self.tasks[name]}\r\n"
            return 1, "错误: 系统找不到指定的文件。"
        if verb == "/delete":
            name = args[args.index("/TN") + 1]
            if name in self.tasks:
                del self.tasks[name]
                return 0, "成功: 已删除计划任务。"
            return 1, "错误: 系统找不到指定的文件。"
        return 1, "unknown"


class CommandLineTest(TempDataDirTestCase):
    def test_run_command_targets_launcher_with_tray(self):
        command = run_command()
        self.assertIn("--tray", command)
        self.assertIn("campus_login_main.py", command)
        self.assertIn("python", command.lower())

    def test_run_command_accepts_extra_arguments(self):
        command = run_command(("--settings",))
        self.assertIn("--settings", command)
        self.assertIn("campus_login_main.py", command)


class TaskSchedulerBackendTest(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = FakeSchtasks()
        self.backend = TaskSchedulerBackend(COMMAND, task_name="CampusLoginTest", runner=self.runner)

    def test_install_is_idempotent(self):
        self.assertFalse(self.backend.is_installed())
        status = self.backend.install()
        self.assertTrue(status.installed)
        self.assertTrue(self.backend.is_installed())
        self.assertEqual(len(self.runner.tasks), 1)
        self.assertIn("campus_login_main.py", self.runner.tasks["CampusLoginTest"])

        self.backend.install()
        self.assertEqual(len(self.runner.tasks), 1, "重复安装不能产生两个启动项")

    def test_uninstall_is_idempotent(self):
        self.backend.install()
        status = self.backend.uninstall()
        self.assertFalse(status.installed)
        self.assertFalse(self.backend.is_installed())
        again = self.backend.uninstall()
        self.assertFalse(again.installed)
        self.assertIn("不存在", again.detail)

    def test_install_uses_xml_config(self):
        """安装必须走 XML 方式：只有这样才能关掉“电池供电不启动/切换电池即停止”。"""
        self.backend.install()
        create_args = self.runner.calls[0]
        self.assertIn("/XML", create_args)
        self.assertIn("/F", create_args)
        self.assertIn("<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>", self.runner.xml_seen)
        self.assertIn("<StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>", self.runner.xml_seen)
        self.assertIn("<LogonTrigger>", self.runner.xml_seen)

    def test_failure_raises_error(self):
        backend = TaskSchedulerBackend(COMMAND, runner=FakeSchtasks(fail=True))
        with self.assertRaises(AutostartError):
            backend.install()

    def test_current_command_is_parsed(self):
        self.backend.install()
        current = self.backend.current_command()
        self.assertIn("pythonw.exe", current)
        self.assertIn("--tray", current)


class FakeRegistry:
    """内存中的假注册表：用于在不写真实注册表的前提下测试读写逻辑。"""

    HKEY_CURRENT_USER = "HKCU"
    KEY_READ = 0x20019
    KEY_SET_VALUE = 0x0002
    REG_SZ = 1

    class Key:
        def __init__(self, registry, path: str) -> None:
            self.registry = registry
            self.path = path

        def __enter__(self):
            return self

        def __exit__(self, *exc) -> bool:
            return False

    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}
        self.keys: set[str] = set()

    def OpenKey(self, root, path, reserved=0, access=0):  # noqa: N802
        if path not in self.keys:
            raise FileNotFoundError(f"key not found: {path}")
        return self.Key(self, path)

    def CreateKeyEx(self, root, path, reserved=0, access=0):  # noqa: N802
        self.keys.add(path)
        return self.Key(self, path)

    def QueryValueEx(self, key, name):  # noqa: N802
        if (key.path, name) not in self.values:
            raise FileNotFoundError(f"value not found: {name}")
        return self.values[(key.path, name)], self.REG_SZ

    def SetValueEx(self, key, name, reserved, value_type, value):  # noqa: N802
        self.values[(key.path, name)] = value

    def DeleteValue(self, key, name):  # noqa: N802
        if (key.path, name) not in self.values:
            raise FileNotFoundError(f"value not found: {name}")
        del self.values[(key.path, name)]


class RegistryBackendTest(unittest.TestCase):
    """使用内存假注册表，避免影响真实启动项。"""

    key_path = r"Software\CampusLoginSelfTest"
    value_name = "CampusLoginSelfTest"

    def setUp(self) -> None:
        self.registry = FakeRegistry()
        self.backend = RegistryBackend(
            COMMAND,
            key_path=self.key_path,
            value_name=self.value_name,
            registry=self.registry,
        )

    def test_install_uninstall_round_trip(self):
        self.assertFalse(self.backend.is_installed())
        status = self.backend.install()
        self.assertTrue(status.installed)
        self.assertTrue(self.backend.is_installed())
        self.assertEqual(self.backend.current_command(), COMMAND)
        self.backend.install()  # 幂等：只有一个值
        self.assertEqual(self.backend.current_command(), COMMAND)
        self.backend.uninstall()
        self.assertFalse(self.backend.is_installed())
        self.backend.uninstall()  # 重复卸载不报错
        self.assertFalse(self.backend.is_installed())


class AutostartManagerTest(TempDataDirTestCase):
    def test_sync_installs_and_removes(self):
        runner = FakeSchtasks()
        manager = AutostartManager(
            Config(),
            command=COMMAND,
            runner=runner,
            backend_instance=TaskSchedulerBackend(COMMAND, task_name="CampusLoginSync", runner=runner),
        )
        config = Config()
        config.auto_start_on_boot = True
        manager.config = config
        manager.sync()
        self.assertTrue(manager.is_installed())
        config.auto_start_on_boot = False
        manager.sync()
        self.assertFalse(manager.is_installed())

    def test_unsupported_platform_is_reported(self):
        manager = AutostartManager(Config())
        status = manager.status()
        self.assertTrue(status.backend)
        self.assertIn("开机自动运行", status.describe())

    def test_falls_back_to_registry_when_task_scheduler_fails(self):
        runner = FakeSchtasks(fail=True)
        registry = FakeRegistry()
        failing_task = TaskSchedulerBackend(COMMAND, task_name="CampusLoginFallback", runner=runner)
        registry_backend = RegistryBackend(
            COMMAND,
            key_path=r"Software\CampusLoginFallback",
            value_name="CampusLoginFallback",
            registry=registry,
        )
        manager = AutostartManager(Config(), command=COMMAND)
        manager.backends = [failing_task, registry_backend]
        manager.backend = failing_task
        status = manager.install()
        self.assertTrue(status.installed)
        self.assertEqual(status.backend, "registry")
        self.assertTrue(manager.is_installed())
        manager.uninstall()
        self.assertFalse(manager.is_installed())


@unittest.skipUnless(
    os.environ.get("CAMPUSLOGIN_TEST_REAL_TASKS") == "1",
    "默认不修改系统任务计划（设置 CAMPUSLOGIN_TEST_REAL_TASKS=1 可运行真实安装/卸载测试）",
)
class RealTaskSchedulerTest(unittest.TestCase):
    task_name = "CampusLoginSelfTest"

    def test_real_install_and_uninstall(self):
        backend = TaskSchedulerBackend("cmd.exe /c exit", task_name=self.task_name)
        self.addCleanup(backend.uninstall)
        backend.install()
        self.assertTrue(backend.is_installed())
        backend.uninstall()
        self.assertFalse(backend.is_installed())


@unittest.skipUnless(
    os.environ.get("CAMPUSLOGIN_TEST_REAL_REGISTRY") == "1",
    "默认不写真实注册表（设置 CAMPUSLOGIN_TEST_REAL_REGISTRY=1 可运行真实注册表测试）",
)
class RealRegistryTest(unittest.TestCase):
    key_path = r"Software\CampusLoginSelfTest"
    value_name = "CampusLoginSelfTest"

    def test_real_install_and_uninstall(self):
        backend = RegistryBackend(COMMAND, key_path=self.key_path, value_name=self.value_name)
        self.addCleanup(backend.uninstall)
        backend.install()
        self.assertTrue(backend.is_installed())
        backend.uninstall()
        self.assertFalse(backend.is_installed())


if __name__ == "__main__":
    unittest.main()
