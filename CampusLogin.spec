# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置。

打包结果：dist/CampusLogin.exe（单文件、无控制台窗口、带托盘图标）
用法：python -m PyInstaller --clean --noconfirm CampusLogin.spec
"""

from pathlib import Path

project_dir = Path(SPECPATH).resolve()
icon = project_dir / "campus_login" / "assets" / "tray.ico"

a = Analysis(
    [str(project_dir / "campus_login_main.py")],
    pathex=[str(project_dir)],
    binaries=[],
    datas=[
        (str(icon), "campus_login/assets"),
    ],
    hiddenimports=[
        "campus_login.cli",
        "campus_login.settings_gui",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 只保留必要模块，减小体积
    excludes=[
        "numpy",
        "PIL",
        "matplotlib",
        "pandas",
        "scipy",
        "PyQt5",
        "PySide6",
        "pytest",
        "test",
        "unittest",
        "tools",
        "tests",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

# 1) 托盘/后台使用的无控制台程序（开机启动使用这个）
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="CampusLogin",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,          # 无控制台窗口（托盘后台运行）
    disable_windowed_traceback=False,
    icon=str(icon) if icon.exists() else None,
    version=None,
)

# 2) 命令行使用的带控制台程序（--status / --login / --install ... 可以直接看到输出）
cli_exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="CampusLoginCLI",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    icon=str(icon) if icon.exists() else None,
    version=None,
)
