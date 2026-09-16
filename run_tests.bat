@echo off
cd /d "%~dp0"
echo Running unit tests (offline, uses a local fake campus portal) ...
echo.
python -m unittest discover -s tests -t .
echo.
pause
