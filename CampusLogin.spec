# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（文件夹模式 / onedir）。

打包结果：dist/CampusLogin/ 文件夹，里面有
    CampusLogin.exe       无控制台窗口，双击进托盘（开机启动用这个）
    CampusLoginCLI.exe    带控制台窗口，用 --status / --login / --install 等命令
    _internal/            运行库（两个 exe 共用）

用法：python -m PyInstaller --clean --noconfirm CampusLogin.spec

为什么用文件夹模式而不是单文件：
  * 单文件 exe 每次启动都要把自己解压到临时目录；而程序带“每 5 分钟自愈检查”，
    一天要启动近 300 次，会带来十几 GB 的临时写入和额外 CPU；
  * 文件夹模式启动快（不用解压），杀毒软件误报也少。
  分发时把整个 dist\\CampusLogin 文件夹压成 zip 给同学即可。
"""

from pathlib import Path

project_dir = Path(SPECPATH).resolve()
icon = project_dir / "campus_login" / "assets" / "tray.ico"

a = Analysis(
    [str(project_dir / "campus_login_main.py")],
    pathex=[str(project_dir)],
    binaries=[],
    datas=[
        # 托盘图标：确保运行时的 icon_path() 能找到（同时放两份，路径最保险）
        (str(icon), "assets"),
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
#    exclude_binaries=True + 下面的 COLLECT = 文件夹模式（不是单文件）
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
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
    [],
    exclude_binaries=True,
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

# 3) 把两个 exe 和共用的运行库放进同一个文件夹
coll = COLLECT(
    exe,
    cli_exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="CampusLogin",
)
