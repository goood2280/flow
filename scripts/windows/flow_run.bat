@echo off
rem ---------------------------------------------------------------------------
rem Flow server runner with automatic restart (the watchdog).
rem   - Runs scripts\flow_server.py (the Python supervisor). The supervisor starts
rem     uvicorn, restarts it when it exits, hangs (/health) or overgrows memory,
rem     and writes %FLOW_DATA_ROOT%\logs\uvicorn.log / flow_restarts.log.
rem   - This outer loop only revives the supervisor itself if it ever dies.
rem
rem Miniforge (manual, easiest to revive):
rem     1. Open "Miniforge Prompt"
rem     2. conda activate flow
rem     3. "%USERPROFILE%\Desktop\flow\scripts\windows\flow_run.bat"
rem   Closing that window stops Flow. To bring it back, run steps 2-3 again.
rem   Clicking inside the window no longer freezes the server (the supervisor
rem   turns console quick-edit off).
rem Boot-time (no login): install_autostart.ps1 registers this same file.
rem Stop for good: scripts\windows\flow_ctl.bat stop  (or create .flow_stop)
rem ---------------------------------------------------------------------------
setlocal EnableExtensions
chcp 65001 >nul
call "%~dp0flow_env.bat"

rem App root = the folder where setup.py was extracted (two levels above this file).
pushd "%~dp0..\.."
set "FLOW_APP_DIR=%CD%"
if not exist "%FLOW_DATA_ROOT%\logs" mkdir "%FLOW_DATA_ROOT%\logs"
title Flow watchdog - %FLOW_APP_DIR% :%FLOW_PORT%

rem Fail fast with a clear message instead of restarting forever when the
rem Python environment is wrong (missing packages, wrong interpreter).
"%PYTHON_EXE%" -c "import fastapi, uvicorn, polars, psutil" 1>nul 2>nul
if errorlevel 1 goto :pycheck_failed

echo ============================================================
echo  Flow watchdog
echo    app    : %FLOW_APP_DIR%
echo    python : %PYTHON_EXE%
echo    DB     : %FLOW_DB_ROOT%
echo    data   : %FLOW_DATA_ROOT%
echo    url    : http://%COMPUTERNAME%:%FLOW_PORT%
echo    logs   : %FLOW_DATA_ROOT%\logs\uvicorn.log
echo  Stop: scripts\windows\flow_ctl.bat stop   or Ctrl+C here
echo ============================================================

:loop
echo [%date% %time%] starting flow supervisor on %FLOW_HOST%:%FLOW_PORT%
"%PYTHON_EXE%" "%FLOW_APP_DIR%\scripts\flow_server.py" --host %FLOW_HOST% --port %FLOW_PORT% --log-dir "%FLOW_DATA_ROOT%\logs"
set "RC=%ERRORLEVEL%"
rem 0 = stopped on purpose (.flow_stop / --stop), 2 = another supervisor already runs.
if "%RC%"=="0" goto :done
if "%RC%"=="2" goto :done
echo [%date% %time%] supervisor exited code=%RC% - restarting in 5s
echo [%date% %time%] supervisor exited code=%RC% >> "%FLOW_DATA_ROOT%\logs\flow_restarts.log"
timeout /t 5 /nobreak >nul
goto loop

:pycheck_failed
echo [%date% %time%] Python check failed: "%PYTHON_EXE%"
echo   fastapi / uvicorn / polars / psutil could not be imported.
echo   Miniforge: open "Miniforge Prompt", run "conda activate %FLOW_CONDA_ENV%",
echo   then in "%FLOW_APP_DIR%" run "python setup.py install-deps".
echo   Or set PYTHON_EXE to the full path of the right python.exe.
echo [%date% %time%] python check failed: "%PYTHON_EXE%" >> "%FLOW_DATA_ROOT%\logs\flow_restarts.log"
popd
exit /b 3

:done
echo [%date% %time%] flow supervisor finished (code=%RC%)
popd
exit /b %RC%
