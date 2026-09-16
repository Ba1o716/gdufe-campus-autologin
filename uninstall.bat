@echo off
setlocal
cd /d "%~dp0"

echo ============================================================
echo  CampusLogin - uninstall  (disable auto start at logon)
echo ============================================================
echo.

set "PY="
where python.exe >nul 2>nul && set "PY=python"
if "%PY%"=="" (
    where py.exe >nul 2>nul && set "PY=py"
)

if not "%PY%"=="" (
    echo [1/2] Disabling auto start ...
    %PY% campus_login_main.py --uninstall
) else (
    echo [1/2] Python not found, skip auto start cleanup.
)

echo [2/2] Stopping the running program ...
taskkill /IM pythonw.exe /F >nul 2>nul
taskkill /IM CampusLogin.exe /F >nul 2>nul

echo.
echo Done.
echo Your saved account/password is kept in %%APPDATA%%\CampusLogin
echo and in Windows Credential Manager (CampusLogin/Credentials).
echo Remove them manually if you want a full cleanup.
echo.
pause
endlocal
