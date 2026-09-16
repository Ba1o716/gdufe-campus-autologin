@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ============================================================
echo  校园网自动登录 - 安装（设置开机自动运行）
echo ============================================================
echo.

set "PY="
where pythonw.exe >nul 2>nul && set "PY=python"
if "%PY%"=="" (
    where py.exe >nul 2>nul && set "PY=py"
)
if "%PY%"=="" (
    echo [错误] 没有找到 Python。请先安装 Python 3.9 以上版本，
    echo        安装时记得勾选 "Add python.exe to PATH"。
    pause
    exit /b 1
)

echo [1/3] 检查运行依赖 ...
%PY% -m pip install --quiet --disable-pip-version-check -r requirements.txt

echo [2/3] 设置开机自动运行 ...
%PY% campus_login_main.py --install
if errorlevel 1 (
    echo.
    echo [提示] 设置开机自动运行失败，请看上面的错误信息。
    pause
    exit /b 1
)

echo [3/3] 是否现在填写校园网账号和密码？（密码不会显示，也不会写入任何文件）
set /p ANSWER=输入 Y 继续，直接回车跳过：
if /i "%ANSWER%"=="Y" %PY% campus_login_main.py --credential

echo.
echo 完成！程序会在你下次登录 Windows 时自动在后台运行。
echo 现在也可以双击 run_tray.bat 立即启动。
echo.
pause
endlocal
