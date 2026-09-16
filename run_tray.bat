@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PY="
where pythonw.exe >nul 2>nul && set "PY=pythonw"
if "%PY%"=="" set "PY=python"
start "" %PY% "%~dp0campus_login_main.py" --tray
