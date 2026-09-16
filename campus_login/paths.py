"""路径、可执行文件与开机启动命令行相关的工具。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Sequence

from . import APP_NAME


def is_frozen() -> bool:
    """是否运行在 PyInstaller 打包后的 exe 中。"""
    return bool(getattr(sys, "frozen", False))


def project_root() -> Path:
    """源码运行时的项目根目录（即包含 campus_login_main.py 的目录）。"""
    return Path(__file__).resolve().parent.parent


def python_gui_executable() -> str:
    """返回不弹控制台窗口的解释器路径（pythonw.exe 优先）。"""
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe":
        return str(exe)
    candidate = exe.with_name("pythonw.exe")
    return str(candidate) if candidate.exists() else str(exe)


def resource_root() -> Path:
    """打包后资源（图标等）所在目录。"""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent


def icon_path() -> Path:
    """托盘图标文件位置。

    打包（PyInstaller）后资源可能被放在 _MEIPASS 根目录，也可能保留
    campus_login/assets 这层目录，所以这里把几种常见位置都试一遍，
    避免“打包后托盘图标变成系统默认图标”这类问题。
    """
    root = resource_root()
    candidates = [
        root / "assets" / "tray.ico",
        root / "campus_login" / "assets" / "tray.ico",
        Path(__file__).resolve().parent / "assets" / "tray.ico",
    ]
    for candidate in candidates:
        try:
            if candidate.exists():
                return candidate
        except OSError:
            continue
    return candidates[0]


def launcher_script() -> Path:
    """源码运行时的入口脚本（可被 pythonw.exe 直接执行）。"""
    return project_root() / "campus_login_main.py"


_DATA_DIR_CACHE: Path | None = None
_DATA_DIR_KEY: str | None = None


def _data_dir_candidates() -> list[Path]:
    """按优先级列出可用的数据目录候选。"""
    candidates: list[Path] = []
    override = os.environ.get("CAMPUSLOGIN_DATA_DIR")
    if override:
        candidates.append(Path(override))
    for env_name in ("APPDATA", "LOCALAPPDATA", "TEMP", "TMP"):
        base = os.environ.get(env_name)
        if base:
            candidates.append(Path(base) / APP_NAME)
    candidates.append(Path.home() / "AppData" / "Roaming" / APP_NAME)
    candidates.append(Path.cwd() / f"{APP_NAME}-data")
    # 去重并保持顺序
    unique: list[Path] = []
    for item in candidates:
        if item not in unique:
            unique.append(item)
    return unique


def _writable(directory: Path) -> bool:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except Exception:
        return False


def data_dir() -> Path:
    """用户数据目录（默认 %APPDATA%\\CampusLogin）。

    如果该目录不可写（权限异常、磁盘满、被安全软件拦截等），自动改用其它可用位置，
    保证程序不会因为“写不进配置目录”而启动失败。可用环境变量覆盖，便于测试。
    """
    global _DATA_DIR_CACHE, _DATA_DIR_KEY
    override = os.environ.get("CAMPUSLOGIN_DATA_DIR") or ""
    if _DATA_DIR_CACHE is not None and _DATA_DIR_KEY == override:
        return _DATA_DIR_CACHE

    candidates = _data_dir_candidates()
    for candidate in candidates:
        if _writable(candidate):
            _DATA_DIR_CACHE = candidate
            _DATA_DIR_KEY = override
            return candidate

    # 全部不可写：返回第一个候选，后续写入会报错并记录日志，但不在这里崩溃
    fallback = candidates[0]
    _DATA_DIR_CACHE = fallback
    _DATA_DIR_KEY = override
    return fallback


def config_path() -> Path:
    return data_dir() / "config.json"


def log_dir() -> Path:
    path = data_dir() / "logs"
    try:
        path.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return path


def log_path() -> Path:
    return log_dir() / "app.log"


def credential_file() -> Path:
    return data_dir() / "credentials.bin"


def launch_args(extra_args: Sequence[str] = ("--tray",)) -> list[str]:
    """启动本程序的命令参数列表（打包后为 exe，源码运行时为 pythonw + 入口脚本）。"""
    if is_frozen():
        executable = Path(sys.executable).resolve()
        # 打包时同时生成 CampusLogin.exe（无控制台，适合开机启动）和
        # CampusLoginCLI.exe（带控制台，适合命令行）；开机启动一律使用前者。
        if executable.name.lower() not in ("campuslogin.exe",):
            sibling = executable.with_name("CampusLogin.exe")
            if sibling.exists():
                executable = sibling
        return [str(executable), *extra_args]
    return [python_gui_executable(), str(launcher_script().resolve()), *extra_args]


def run_command(extra_args: Sequence[str] = ("--tray",)) -> str:
    """开机启动项使用的完整命令行（已按 Windows 规则加引号）。"""
    return subprocess.list2cmdline(launch_args(extra_args))


def is_console_python() -> bool:
    return Path(sys.executable).name.lower() == "python.exe"
