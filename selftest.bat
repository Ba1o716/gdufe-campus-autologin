@echo off
cd /d "%~dp0"
echo Running offline self-test (no internet, no password needed) ...
echo.
python campus_login_main.py --selftest
echo.
pause
