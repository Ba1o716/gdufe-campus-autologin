@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 运行自动化测试（使用本机模拟校园网门户，不联网）
python -m unittest discover -s tests -t .
pause
