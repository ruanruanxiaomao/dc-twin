@echo off
rem Launch the DC Twin Studio desktop cockpit without a console window.
setlocal
cd /d "%~dp0"

set "PYW=pythonw"
where pythonw >nul 2>nul
if errorlevel 1 set "PYW=python"

start "" "%PYW%" "%~dp0desktop_app.pyw"
if errorlevel 1 (
  echo Failed to start. Falling back to python desktop_app.pyw
  python "%~dp0desktop_app.pyw"
)
endlocal
