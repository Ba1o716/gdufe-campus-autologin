@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ============================================================
echo  打包 CampusLogin.exe（需要一次联网安装 PyInstaller）
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

echo [1/3] 安装 requests / pyinstaller ...
%PY% -m pip install --quiet --disable-pip-version-check -r requirements.txt pyinstaller
if errorlevel 1 (
    echo [错误] 依赖安装失败（打包只在这一步需要联网）。
    pause
    exit /b 1
)

echo [2/3] 清理旧的打包结果 ...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo [3/3] 开始打包 ...
%PY% -m PyInstaller --clean --noconfirm CampusLogin.spec
if errorlevel 1 (
    echo [错误] 打包失败。
    pause
    exit /b 1
)

echo.
echo 打包完成：dist\CampusLogin.exe
echo   * 双击即可在托盘后台运行（无控制台窗口）
echo   * 命令行：dist\CampusLogin.exe --status / --login / --settings / --install / --uninstall
echo.
pause
endlocal
