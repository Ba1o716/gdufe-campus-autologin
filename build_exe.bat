@echo off
setlocal
cd /d "%~dp0"

echo ============================================================
echo  CampusLogin - build CampusLogin.exe  (needs internet once)
echo ============================================================
echo.

set "PY="
where python.exe >nul 2>nul && set "PY=python"
if "%PY%"=="" (
    where py.exe >nul 2>nul && set "PY=py"
)
if "%PY%"=="" (
    echo [ERROR] Python not found.
    echo         Install Python 3.9+ and tick "Add python.exe to PATH".
    pause
    exit /b 1
)

echo [1/3] Installing dependencies: requests, pyinstaller ...
%PY% -m pip install --quiet --disable-pip-version-check -r requirements.txt pyinstaller
if errorlevel 1 (
    echo [ERROR] pip install failed. Check your internet or proxy settings.
    pause
    exit /b 1
)

echo [2/3] Cleaning old build output ...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo [3/3] Building, this may take 1-2 minutes ...
%PY% -m PyInstaller --clean --noconfirm CampusLogin.spec
if errorlevel 1 (
    echo [ERROR] Build failed. See the messages above.
    pause
    exit /b 1
)

echo.
echo Done.
echo   dist\CampusLogin.exe      - tray mode, no console window
echo   dist\CampusLoginCLI.exe   - console mode, for --status / --login
echo.
pause
endlocal
