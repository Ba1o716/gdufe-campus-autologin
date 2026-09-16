@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 运行离线自检（不会访问真实校园网，也不需要账号密码）
python campus_login_main.py --selftest
pause
