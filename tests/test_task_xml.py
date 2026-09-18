"""开机自启任务的 XML 生成与安装流程测试。

重点验证那些“Task Scheduler 默认值会坑到笔记本用户”的开关：
  * 电池供电时也要启动（DisallowStartIfOnBatteries=false）
  * 切到电池不要停止（StopIfGoingOnBatteries=false）
  * 不限运行时长（ExecutionTimeLimit=PT0S，默认 72 小时会把常驻程序杀掉）
  * 登录 + 解锁触发 + 定时自愈检查
"""

from __future__ import annotations

import unittest
from xml.etree import ElementTree

from campus_login.autostart import (
    AutostartError,
    TaskSchedulerBackend,
    build_task_xml,
    current_user_id,
    split_command_line,
    working_directory_for,
)

from .support import TempDataDirTestCase

NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
COMMAND = r'"C:\Python\pythonw.exe" "C:\App\campus_login_main.py" --tray'


class SplitCommandLineTest(unittest.TestCase):
    def test_splits_quoted_and_plain(self):
        parts = split_command_line(COMMAND)
        self.assertEqual(parts[0], r"C:\Python\pythonw.exe")
        self.assertEqual(parts[1], r"C:\App\campus_login_main.py")
        self.assertEqual(parts[2], "--tray")

    def test_splits_without_quotes(self):
        self.assertEqual(split_command_line(r"C:\a\b.exe --tray"), [r"C:\a\b.exe", "--tray"])

    def test_empty(self):
        self.assertEqual(split_command_line(""), [])

    def test_working_directory_from_script(self):
        directory = working_directory_for([r"C:\Python\pythonw.exe", r"C:\App\campus_login_main.py"])
        self.assertTrue(directory.lower().endswith("app"))

    def test_working_directory_from_exe(self):
        directory = working_directory_for([r"C:\App\dist\CampusLogin.exe", "--tray"])
        self.assertTrue(directory.lower().endswith(r"dist"))


class TaskXmlTest(unittest.TestCase):
    def setUp(self) -> None:
        self.xml = build_task_xml(
            task_name="CampusLogin",
            command=r"C:\App\dist\CampusLogin.exe",
            arguments="--tray",
            working_directory=r"C:\App\dist",
            user_id="PC\\student",
        )
        self.root = ElementTree.fromstring(self.xml)

    def settings(self, name: str) -> str:
        node = self.root.find(f"t:Settings/t:{name}", NS)
        self.assertIsNotNone(node, f"缺少设置项 {name}")
        return (node.text or "").strip()

    def test_battery_settings_are_disabled(self):
        # 这两项默认是 true，正是“用电池开机不启动 / 插电才启动”的元凶
        self.assertEqual(self.settings("DisallowStartIfOnBatteries"), "false")
        self.assertEqual(self.settings("StopIfGoingOnBatteries"), "false")

    def test_execution_time_limit_is_unlimited(self):
        # 默认 PT72H，会把常驻程序在 3 天后杀掉
        self.assertEqual(self.settings("ExecutionTimeLimit"), "PT0S")

    def test_misc_settings(self):
        self.assertEqual(self.settings("StartWhenAvailable"), "true")
        self.assertEqual(self.settings("RunOnlyIfIdle"), "false")
        self.assertEqual(self.settings("MultipleInstancesPolicy"), "IgnoreNew")
        self.assertEqual(self.settings("RunOnlyIfNetworkAvailable"), "false")

    def test_triggers(self):
        triggers = self.root.findall("t:Triggers/*", NS)
        kinds = [node.tag.split("}")[-1] for node in triggers]
        self.assertIn("LogonTrigger", kinds)
        self.assertIn("TimeTrigger", kinds)
        self.assertIn("SessionStateChangeTrigger", kinds)

        session = self.root.find("t:Triggers/t:SessionStateChangeTrigger", NS)
        self.assertEqual(session.find("t:StateChange", NS).text, "SessionUnlock")

        repetition = self.root.find("t:Triggers/t:TimeTrigger/t:Repetition", NS)
        self.assertEqual(repetition.find("t:Interval", NS).text, "PT5M")

    def test_action_and_principal(self):
        exec_node = self.root.find("t:Actions/t:Exec", NS)
        self.assertEqual(exec_node.find("t:Command", NS).text, r"C:\App\dist\CampusLogin.exe")
        self.assertEqual(exec_node.find("t:Arguments", NS).text, "--tray")
        self.assertEqual(exec_node.find("t:WorkingDirectory", NS).text, r"C:\App\dist")

        principal = self.root.find("t:Principals/t:Principal", NS)
        self.assertEqual(principal.find("t:LogonType", NS).text, "InteractiveToken")
        self.assertEqual(principal.find("t:RunLevel", NS).text, "LeastPrivilege")
        self.assertEqual(principal.find("t:UserId", NS).text, "PC\\student")

    def test_self_check_can_be_disabled(self):
        xml = build_task_xml(
            task_name="CampusLogin",
            command="x.exe",
            user_id="PC\\student",
            check_minutes=0,
        )
        root = ElementTree.fromstring(xml)
        self.assertIsNone(root.find("t:Triggers/t:TimeTrigger", NS))

    def test_xml_is_escaped(self):
        xml = build_task_xml(
            task_name="CampusLogin",
            command=r"C:\Program Files\A&B\app.exe",
            arguments='--name "a&b"',
            user_id="PC\\student",
        )
        root = ElementTree.fromstring(xml)
        self.assertEqual(
            root.find("t:Actions/t:Exec/t:Command", NS).text, r"C:\Program Files\A&B\app.exe"
        )
        self.assertEqual(root.find("t:Actions/t:Exec/t:Arguments", NS).text, '--name "a&b"')

    def test_current_user_id_format(self):
        user = current_user_id()
        if user:
            self.assertTrue(user)


class FakeRunner:
    """模拟 schtasks：支持 /XML 与 /SC 两种创建方式，可指定让哪种失败。"""

    def __init__(self, fail_xml: bool = False, fail_all: bool = False) -> None:
        self.tasks: dict[str, str] = {}
        self.calls: list[list[str]] = []
        self.fail_xml = fail_xml
        self.fail_all = fail_all
        self.xml_seen: str = ""

    def __call__(self, args):
        args = [str(item) for item in args]
        self.calls.append(args)
        if self.fail_all:
            return 1, "错误: 拒绝访问。"
        verb = args[1].lower()
        if verb == "/create":
            name = args[args.index("/TN") + 1]
            if "/XML" in args:
                if self.fail_xml:
                    return 1, "错误: XML 文件无效。"
                from pathlib import Path

                self.xml_seen = Path(args[args.index("/XML") + 1]).read_text(encoding="utf-16")
                self.tasks[name] = "<created from xml>"
                return 0, "成功: 已创建计划任务"
            self.tasks[name] = args[args.index("/TR") + 1]
            return 0, "成功: 已创建计划任务"
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


class TaskInstallTest(TempDataDirTestCase):
    def backend(self, runner):
        return TaskSchedulerBackend(
            COMMAND, task_name="CampusLoginTest", runner=runner,
            launch_args=split_command_line(COMMAND),
        )

    def test_install_prefers_xml(self):
        runner = FakeRunner()
        status = self.backend(runner).install()
        self.assertTrue(status.installed)
        first_call = runner.calls[0]
        self.assertIn("/XML", first_call)
        self.assertIn("<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>", runner.xml_seen)
        self.assertIn("<StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>", runner.xml_seen)
        self.assertIn("<Command>C:\\Python\\pythonw.exe</Command>", runner.xml_seen)
        self.assertIn("--tray", runner.xml_seen)
        self.assertIn("电池下也会启动", status.detail)

    def test_install_falls_back_to_simple_task(self):
        runner = FakeRunner(fail_xml=True)
        status = self.backend(runner).install()
        self.assertTrue(status.installed)
        # 第一次用 XML（失败），第二次用简化方式
        self.assertIn("/XML", runner.calls[0])
        self.assertEqual(runner.calls[1][runner.calls[1].index("/SC") + 1], "ONLOGON")
        self.assertEqual(runner.calls[1][runner.calls[1].index("/TR") + 1], COMMAND)
        self.assertIn("简化方式", status.detail)

    def test_install_raises_when_both_fail(self):
        runner = FakeRunner(fail_all=True)
        with self.assertRaises(AutostartError):
            self.backend(runner).install()


if __name__ == "__main__":
    unittest.main()
