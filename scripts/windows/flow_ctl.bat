@echo off
rem ---------------------------------------------------------------------------
rem Flow control:  flow_ctl.bat status | stop | restart | log | health
rem   status   supervisor + server state (JSON)
rem   stop     stop the server and the watchdog (requests it, returns at once)
rem   restart  restart only the server process (e.g. after an update)
rem   log      show the last 60 lines of uvicorn.log
rem   health   call /health on this machine
rem Uses the same environment as flow_run.bat (flow_env.bat).
rem ---------------------------------------------------------------------------
setlocal EnableExtensions
chcp 65001 >nul
call "%~dp0flow_env.bat"
pushd "%~dp0..\.."
set "ACTION=%~1"
if "%ACTION%"=="" set "ACTION=status"

if /i "%ACTION%"=="status"  goto :status
if /i "%ACTION%"=="stop"    goto :stop
if /i "%ACTION%"=="restart" goto :restart
if /i "%ACTION%"=="log"     goto :log
if /i "%ACTION%"=="health"  goto :health
echo usage: flow_ctl.bat status ^| stop ^| restart ^| log ^| health
popd
exit /b 1

:status
"%PYTHON_EXE%" scripts\flow_server.py --status --port %FLOW_PORT% --log-dir "%FLOW_DATA_ROOT%\logs"
goto :end
:stop
"%PYTHON_EXE%" scripts\flow_server.py --stop --port %FLOW_PORT% --log-dir "%FLOW_DATA_ROOT%\logs"
goto :end
:restart
"%PYTHON_EXE%" scripts\flow_server.py --restart --port %FLOW_PORT% --log-dir "%FLOW_DATA_ROOT%\logs"
goto :end
:log
powershell -NoProfile -Command "Get-Content -Tail 60 -Encoding UTF8 '%FLOW_DATA_ROOT%\logs\uvicorn.log'"
goto :end
:health
powershell -NoProfile -Command "try { (Invoke-WebRequest -UseBasicParsing -TimeoutSec 10 'http://127.0.0.1:%FLOW_PORT%/health').Content } catch { Write-Host ('health check failed: ' + $_.Exception.Message); exit 1 }"
goto :end

:end
set "RC=%ERRORLEVEL%"
popd
exit /b %RC%
