@echo off
setlocal
title Surveillance.ai

pushd "%~dp0"
if errorlevel 1 (
    echo Could not open the Surveillance.ai project folder.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" goto setup

echo Checking the installation...
".venv\Scripts\python.exe" -c "import survai.web; import survai.multi_camera" >nul 2>&1
if errorlevel 1 goto setup
goto launch

:setup
echo The installation is incomplete. Running Setup Surveillance.bat...
echo.
call "%~dp0Setup Surveillance.bat"
if errorlevel 1 goto setup_failed

:launch

if not exist "walkout.multi.yaml" (
    copy "walkout.multi.example.yaml" "walkout.multi.yaml" >nul
    if errorlevel 1 (
        echo Could not create walkout.multi.yaml from the example configuration.
        popd
        pause
        exit /b 1
    )
)

echo Starting Surveillance.ai. The dashboard will open in your browser.
echo Keep this window open while monitoring. Press Ctrl+C to stop.
echo.
".venv\Scripts\python.exe" -m survai.main --web
set "survai_exit_code=%errorlevel%"

if not "%survai_exit_code%"=="0" (
    echo.
    echo Surveillance.ai stopped with an error. See the message above.
    echo If dependencies are missing, run this in PowerShell:
    echo   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
)

popd
exit /b %survai_exit_code%

:setup_failed
echo.
echo The dashboard cannot start until setup completes successfully.
popd
exit /b 1
