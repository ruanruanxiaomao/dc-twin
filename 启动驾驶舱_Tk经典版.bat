@echo off
rem 旧的 Tk 驾驶舱（保留：无 WebView2 的机器上仍可用）
setlocal
cd /d "%~dp0"
set "PYW=pythonw"
where pythonw >nul 2>nul
if errorlevel 1 set "PYW=python"
start "" "%PYW%" "%~dp0desktop_app.pyw"
endlocal
