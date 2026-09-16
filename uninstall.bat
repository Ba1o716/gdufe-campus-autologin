@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ============================================================
echo  校园网自动登录 - 卸载（取消开机自动运行）
echo ============================================================
echo.

set "PY="
where python.exe >nul 2>nul && set "PY=python"
if "%PY%"=="" (
    where py.exe >nul 2>nul && set "PY=py"
)
if "%PY%"=="" (
    echo [错误] 没有找到 Python。
    pause
    exit /b 1
)

echo [1/2] 取消开机自动运行 ...
%PY% campus_login_main.py --uninstall

echo [2/2] 结束正在运行的后台程序 ...
taskkill /IM pythonw.exe /F >nul 2>nul
taskkill /IM CampusLogin.exe /F >nul 2>nul

echo.
echo 已完成。是否同时删除保存的账号密码和配置？（Y = 删除）
set /p ANSWER=输入 Y 删除，直接回车保留：
if /i "%ANSWER%"=="Y" (
    if exist "%APPDATA%\CampusLogin" (
        rmdir /s /q "%APPDATA%\CampusLogin"
        echo 已删除 %APPDATA%\CampusLogin
    )
    echo 已删除保存的账号密码（Windows 凭据管理器中的条目可在“凭据管理器 → Windows 凭据”中手动删除 CampusLogin/Credentials）
)

echo.
echo 卸载完成。程序文件夹可以整个删除。
pause
endlocal
