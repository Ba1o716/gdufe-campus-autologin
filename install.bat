@echo off
setlocal
cd /d "%~dp0"

echo ============================================================
echo  CampusLogin - install  (enable auto start at logon)
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

echo [1/3] Installing dependencies ...
%PY% -m pip install --quiet --disable-pip-version-check -r requirements.txt

echo [2/3] Enabling auto start at logon ...
%PY% campus_login_main.py --install
if errorlevel 1 (
    echo [ERROR] Failed to enable auto start. See messages above.
    pause
    exit /b 1
)

echo [3/3] Opening the settings window ...
%PY% campus_login_main.py --settings

echo.
echo Done. The program will start automatically at next logon.
echo To start it right now, double-click run_tray.bat
echo.
pause
endlocal
