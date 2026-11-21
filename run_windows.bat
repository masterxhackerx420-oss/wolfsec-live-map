@echo off
REM WolfSec LiveMap - one-click launcher for Windows 10/11
setlocal
cd /d "%~dp0"
title WolfSec LiveMap
if not defined PORT set PORT=8765

REM ---- find Python 3 (the "py" launcher first; plain "python" may be the Microsoft Store stub)
set "PY="
py -3 -c "import sys" >nul 2>&1 && set "PY=py -3"
if defined PY goto have_python
python -c "import sys; sys.exit(0 if sys.version_info[0]==3 else 1)" >nul 2>&1 && set "PY=python"
if defined PY goto have_python
if exist "%LocalAppData%\Programs\Python\Python312\python.exe" set PY="%LocalAppData%\Programs\Python\Python312\python.exe"
if defined PY goto have_python

echo.
echo  Python 3 is not installed on this PC.
where winget >nul 2>&1
if errorlevel 1 goto manual_python
choice /C YN /M " Install Python 3.12 now using winget (free, from python.org)"
if errorlevel 2 goto manual_python
winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
if exist "%LocalAppData%\Programs\Python\Python312\python.exe" (
  set PY="%LocalAppData%\Programs\Python\Python312\python.exe"
  goto have_python
)
echo  Python was installed. Close this window and double-click run_windows.bat again.
pause
exit /b 1

:manual_python
echo  Download Python 3.12 from https://www.python.org/downloads/windows/
echo  IMPORTANT: tick "Add python.exe to PATH" in the installer, then run this file again.
start "" https://www.python.org/downloads/windows/
pause
exit /b 1

:have_python
echo [livemap] using Python: %PY%
if not exist ".venv\Scripts\python.exe" (
  echo [livemap] creating virtual environment...
  %PY% -m venv .venv
  if errorlevel 1 ( echo Failed to create the virtual environment. & pause & exit /b 1 )
)
echo [livemap] installing/updating dependencies (first run takes a minute)...
".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r requirements.txt
if errorlevel 1 ( echo pip install failed - check your internet connection. & pause & exit /b 1 )

set LIVEMAP_PORT=%PORT%
set PYTHONUNBUFFERED=1
echo [livemap] starting on http://localhost:%PORT%  - your browser will open. Close this window to stop.
".venv\Scripts\python.exe" -m livemap
pause
