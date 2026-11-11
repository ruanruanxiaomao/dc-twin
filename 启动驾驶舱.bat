@echo off
rem DC Twin — single-window client (pywebview + local console).
setlocal
cd /d "%~dp0"

set "PYW=pythonw"
where pythonw >nul 2>nul
if errorlevel 1 set "PYW=python"

start "" "%PYW%" "%~dp0dc_app.pyw"
if errorlevel 1 (
  echo Failed to start. Falling back to python dc_app.pyw
  python "%~dp0dc_app.pyw"
)
endlocal
