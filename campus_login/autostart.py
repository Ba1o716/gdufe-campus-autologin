"""Windows 开机自动运行。

提供两种“不需要管理员权限”的官方机制：
  1. 任务计划程序（Task Scheduler，默认）：登录时触发，后台无窗口；
  2. 注册表 HKCU\\...\\Run：更简单，同样不需要管理员权限。

安装 / 卸载都是幂等的：重复安装只会有一个启动项，卸载后一定清理干净。
"""

from __future__ import annotations

import locale
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from typing import Callable, Sequence

from .config import Config
from .paths import run_command

log = logging.getLogger("campus_login.autostart")

TASK_NAME = "CampusLogin"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "CampusLogin"

CREATE_NO_WINDOW = 0x08000000


class AutostartError(RuntimeError):
    """设置开机启动失败。"""


@dataclass
class AutostartStatus:
    installed: bool
    backend: str
    command: str
    location: str
    detail: str = ""

    def describe(self) -> str:
        state = "已开启" if self.installed else "未开启"
        return f"开机自动运行：{state}（{self.location}）\n命令：{self.command}"


def _default_runner(args: Sequence[str]):
    """执行外部命令，返回 (returncode, 输出文本)。"""
    creationflags = CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        completed = subprocess.run(
            list(args),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
            check=False,
        )
    except FileNotFoundError as exc:
        raise AutostartError(f"无法执行 {args[0]}：{exc}") from exc
    raw = completed.stdout or b""
    text = raw.decode(locale.getpreferredencoding(False) or "utf-8", errors="replace")
    return completed.returncode, text


class TaskSchedulerBackend:
    """任务计划程序（当前用户，登录时触发，不需要管理员权限）。"""

    name = "task"

    def __init__(
        self,
        command: str,
        task_name: str = TASK_NAME,
        runner: Callable[[Sequence[str]], tuple[int, str]] | None = None,
    ) -> None:
        self.command = command
        self.task_name = task_name
        self.runner = runner or _default_runner

    @property
    def location(self) -> str:
        return f"任务计划程序（任务名 {self.task_name}）"

    def is_installed(self) -> bool:
        try:
            code, _ = self.runner(["schtasks", "/Query", "/TN", self.task_name])
        except AutostartError:
            return False
        return code == 0

    def current_command(self) -> str:
        try:
            code, output = self.runner(
                ["schtasks", "/Query", "/TN", self.task_name, "/FO", "LIST", "/V"]
            )
        except AutostartError:
            return ""
        if code != 0:
            return ""
        for line in output.splitlines():
            if "要运行的任务" in line or "Task To Run" in line:
                _, _, value = line.partition(":")
                return value.strip()
        return ""

    def install(self) -> AutostartStatus:
        args = [
            "schtasks",
            "/Create",
            "/TN",
            self.task_name,
            "/TR",
            self.command,
            "/SC",
            "ONLOGON",
            "/F",
        ]
        code, output = self.runner(args)
        if code != 0:
            message = output.strip().splitlines()[-1] if output.strip() else f"退出码 {code}"
            raise AutostartError(f"创建计划任务失败：{message}")
        # 立刻用 query 复核一次，确保安装真的生效（幂等：只会有一个同名任务）
        installed = self.is_installed()
        return AutostartStatus(True, self.name, self.command, self.location, "已创建/覆盖同名任务" if installed else "创建命令已执行")

    def uninstall(self) -> AutostartStatus:
        if not self.is_installed():
            return AutostartStatus(False, self.name, self.command, self.location, "任务不存在，无需删除")
        code, output = self.runner(["schtasks", "/Delete", "/TN", self.task_name, "/F"])
        if code != 0:
            message = output.strip().splitlines()[-1] if output.strip() else f"退出码 {code}"
            raise AutostartError(f"删除计划任务失败：{message}")
        return AutostartStatus(self.is_installed(), self.name, self.command, self.location, "已删除")


class RegistryBackend:
    """注册表 HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run。"""

    name = "registry"

    def __init__(
        self,
        command: str,
        key_path: str = RUN_KEY,
        value_name: str = RUN_VALUE,
        registry=None,
    ) -> None:
        self.command = command
        self.key_path = key_path
        self.value_name = value_name
        self._registry = registry

    @property
    def location(self) -> str:
        return f"注册表 HKCU\\{self.key_path}\\{self.value_name}"

    def _winreg(self):
        if self._registry is not None:
            return self._registry
        if os.name != "nt":
            raise AutostartError("注册表方式仅适用于 Windows")
        import winreg  # noqa: PLC0415

        return winreg

    def current_command(self) -> str:
        winreg = self._winreg()
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.key_path, 0, winreg.KEY_READ) as key:
                value, _ = winreg.QueryValueEx(key, self.value_name)
                return str(value)
        except FileNotFoundError:
            return ""
        except OSError as exc:
            log.debug("读取注册表失败：%s", exc)
            return ""

    def is_installed(self) -> bool:
        return bool(self.current_command())

    def install(self) -> AutostartStatus:
        winreg = self._winreg()
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, self.key_path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, self.value_name, 0, winreg.REG_SZ, self.command)
        return AutostartStatus(bool(self.current_command()), self.name, self.command, self.location, "已写入")

    def uninstall(self) -> AutostartStatus:
        winreg = self._winreg()
        removed = False
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, self.key_path, 0, winreg.KEY_SET_VALUE
            ) as key:
                winreg.DeleteValue(key, self.value_name)
                removed = True
        except FileNotFoundError:
            removed = False
        except OSError as exc:
            raise AutostartError(f"删除注册表启动项失败：{exc}") from exc
        return AutostartStatus(
            bool(self.current_command()),
            self.name,
            self.command,
            self.location,
            "已删除" if removed else "启动项不存在，无需删除",
        )


class AutostartManager:
    """对外统一的“开机自动运行”入口。"""

    def __init__(
        self,
        config: Config,
        backend: str | None = None,
        runner: Callable[[Sequence[str]], tuple[int, str]] | None = None,
        command: str | None = None,
        logger: logging.Logger | None = None,
        backend_instance=None,
    ) -> None:
        self.config = config
        self.log = logger or log
        self.command = command or run_command(("--tray",))
        self.backend_name = (backend or config.autostart_backend or "task").lower()
        if self.backend_name not in ("task", "registry"):
            self.backend_name = "task"
        if backend_instance is not None:
            self.backends = [backend_instance]
            self.backend_name = getattr(backend_instance, "name", self.backend_name)
        else:
            other = "registry" if self.backend_name == "task" else "task"
            self.backends = [
                self._make_backend(self.backend_name, runner),
                self._make_backend(other, runner),
            ]
        self.backend = self.backends[0]

    def _make_backend(self, name: str, runner):
        if name == "registry":
            return RegistryBackend(self.command)
        return TaskSchedulerBackend(self.command, runner=runner)

    @property
    def location(self) -> str:
        return self.backend.location

    def is_installed(self) -> bool:
        for backend in self.backends:
            try:
                if backend.is_installed():
                    return True
            except Exception as exc:  # 任何异常都不能影响程序启动
                self.log.debug("检查 %s 启动项失败：%s", backend.name, type(exc).__name__)
        return False

    def active_backend(self):
        """返回当前真正生效的启动项后端（都没有时返回首选后端）。"""
        for backend in self.backends:
            try:
                if backend.is_installed():
                    return backend
            except Exception:
                continue
        return self.backend

    def status(self) -> AutostartStatus:
        if not sys.platform.startswith("win"):
            return AutostartStatus(False, self.backend_name, self.command, "非 Windows 系统")
        installed = self.is_installed()
        return AutostartStatus(installed, self.backend_name, self.command, self.backend.location)

    def install(self) -> AutostartStatus:
        """开启开机自动运行（幂等）。首选方式失败时自动改用另一种官方机制。"""
        if not sys.platform.startswith("win"):
            raise AutostartError("开机自动运行仅支持 Windows")
        problems: list[str] = []
        for backend in self.backends:
            try:
                backend.install()
                if backend.is_installed():
                    self.log.info("已设置开机自动运行：%s", backend.location)
                    return AutostartStatus(
                        True, backend.name, self.command, backend.location, "已生效"
                    )
                problems.append(f"{backend.location}：设置后未生效")
            except Exception as exc:
                self.log.debug("%s 设置失败：%s", backend.name, exc)
                problems.append(str(exc))
        raise AutostartError("；".join(problems) or "设置开机自动运行失败")

    def uninstall(self) -> AutostartStatus:
        """关闭开机自动运行（幂等）：两种机制下残留的启动项都会被清理。"""
        if not sys.platform.startswith("win"):
            raise AutostartError("开机自动运行仅支持 Windows")
        removed: list[str] = []
        problems: list[str] = []
        for backend in self.backends:
            try:
                if backend.is_installed():
                    backend.uninstall()
                    removed.append(backend.location)
            except Exception as exc:
                problems.append(f"{backend.location}：{exc}")
        if problems and not removed:
            raise AutostartError("；".join(problems))
        location = removed[0] if removed else self.backend.location
        if removed:
            self.log.info("已取消开机自动运行：%s", location)
        still_installed = self.is_installed()
        detail = "已删除" if removed else "启动项不存在，无需删除"
        return AutostartStatus(still_installed, self.backend_name, self.command, location, detail)

    def sync(self) -> AutostartStatus:
        """按配置里的 auto_start_on_boot 同步开机启动项。"""
        if self.config.auto_start_on_boot:
            return self.install()
        return self.uninstall()
