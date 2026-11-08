@echo off
chcp 65001 >nul
title 配送中心数字孪生 · 控制台
cd /d "%~dp0"

set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"

echo.
echo   配送中心数字孪生 · 控制台
echo   服务地址 http://127.0.0.1:8765/
echo   按 Ctrl+C 停止
echo.

rem 后台延迟 2 秒再打开浏览器，等服务起来
start "" cmd /c "timeout /t 2 >nul & start "" http://127.0.0.1:8765/"

"%PY%" console\server.py

echo.
echo   服务已停止。
pause
