@echo off
setlocal
title Surveillance.ai Setup

pushd "%~dp0"
if errorlevel 1 (
    echo Could not open the Surveillance.ai project folder.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating the Python 3.11 environment...
    py -3.11 -m venv .venv
    if errorlevel 1 goto failed
)

echo Installing Surveillance.ai dependencies. This requires internet access.
echo The first installation may take several minutes.
echo.
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed

".venv\Scripts\python.exe" -c "from httpx import Client, AsyncClient" >nul 2>&1
if errorlevel 1 (
    echo Repairing the incomplete httpx installation...
    ".venv\Scripts\python.exe" -m pip install --force-reinstall --no-deps httpx==0.28.1
    if errorlevel 1 goto failed
)

".venv\Scripts\python.exe" -m pip check
if errorlevel 1 goto failed

".venv\Scripts\python.exe" -c "import survai.web; import survai.multi_camera"
if errorlevel 1 goto failed

".venv\Scripts\python.exe" -m survai.main --selftest
if errorlevel 1 goto failed

echo.
echo Setup complete. Double-click Open Surveillance.bat to start the dashboard.
popd
pause
exit /b 0

:failed
echo.
echo Setup could not finish. Review the error above, then run this file again.
echo Python 3.11 and an internet connection are required.
popd
pause
exit /b 1
