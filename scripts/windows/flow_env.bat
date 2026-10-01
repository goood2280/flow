@echo off
rem ---------------------------------------------------------------------------
rem Flow environment defaults for the Windows server.
rem Storage lives on D: (DB and flow-data). Edit here if the drive changes.
rem Values already set in the environment are kept (not overwritten).
rem ---------------------------------------------------------------------------
if not defined FLOW_STORAGE_ROOT set "FLOW_STORAGE_ROOT=D:\"
if not defined FLOW_DB_ROOT      set "FLOW_DB_ROOT=%FLOW_STORAGE_ROOT%DB"
if not defined FLOW_DATA_ROOT    set "FLOW_DATA_ROOT=%FLOW_STORAGE_ROOT%flow-data"
if not defined FLOW_PROD         set "FLOW_PROD=1"
if not defined FLOW_PORT         set "FLOW_PORT=8080"
if not defined FLOW_HOST         set "FLOW_HOST=0.0.0.0"
rem Single production server: every job (FAB matching scan, Auto report,
rem cache builds) runs in this process. There is no development worker.
rem Resource profile is auto-detected (64GB+ and 8+ cores -> large).
rem Force it with: set FLOW_RESOURCE_PROFILE=large
rem --- Python (Miniforge / conda) -------------------------------------------
rem PYTHON_EXE wins. Otherwise: the active conda env (Miniforge Prompt after
rem "conda activate %FLOW_CONDA_ENV%"), then the named env under the usual
rem Miniforge/Miniconda install folders, then "python" on PATH.
if not defined FLOW_CONDA_ENV set "FLOW_CONDA_ENV=flow"
if not defined PYTHON_EXE if defined CONDA_PREFIX if exist "%CONDA_PREFIX%\python.exe" set "PYTHON_EXE=%CONDA_PREFIX%\python.exe"
if not defined PYTHON_EXE for %%R in ("%ProgramData%\miniforge3" "%USERPROFILE%\miniforge3" "%LOCALAPPDATA%\miniforge3" "%ProgramData%\Miniconda3" "%USERPROFILE%\miniconda3") do if not defined PYTHON_EXE if exist "%%~R\envs\%FLOW_CONDA_ENV%\python.exe" set "PYTHON_EXE=%%~R\envs\%FLOW_CONDA_ENV%\python.exe"
if not defined PYTHON_EXE set "PYTHON_EXE=python"
rem conda python started without "conda activate" still needs its DLL folders.
for %%P in ("%PYTHON_EXE%") do if exist "%%~dpP\Library\bin" set "PATH=%%~dpP;%%~dpP\Library\bin;%%~dpP\Scripts;%PATH%"
rem --- Company websocket login (fill in before go-live) ---------------------
rem FLOW_WS_AUTH_URL        ws(s):// address the browser connects to (login screen)
rem FLOW_WS_AUTH_VERIFY_URL address Flow uses to re-check the token (ws:// or http(s)://)
rem FLOW_WS_AUTH_SEND       optional first message the browser sends after connecting
rem FLOW_WS_AUTH_USER_MAP   company id -> Flow account JSON, e.g. {"example.user":"hol"} (no default;
rem                         put the real id only in flow_env.local.bat)
rem FLOW_DATA_KEY           optional encryption key for people.enc (else <app>\.flow_data.key)
rem Installed servers show only login buttons (no ID/PW form). Emergency: set FLOW_PASSWORD_LOGIN_ENABLED=1
rem Never put a user id in FLOW_WS_AUTH_URL/SEND (everyone would log in as that user; Flow refuses it).
rem Only a URL comes back? FLOW_WS_AUTH_URL_ACTION=open (default) | server (+FLOW_WS_AUTH_FETCH_ALLOW) | browser
rem How to check received frames: AGENTS.md, WebSocket login troubleshooting (FLOW_WS_AUTH_DEBUG=1).
rem set "FLOW_WS_AUTH_URL=wss://auth.example.com/login"
rem set "FLOW_WS_AUTH_VERIFY_URL=wss://auth.example.com/verify"
if not defined PYTHONUTF8        set "PYTHONUTF8=1"
if not defined PYTHONIOENCODING  set "PYTHONIOENCODING=utf-8"
rem Site-specific overrides (LLM keys, login, ports) go in flow_env.local.bat next
rem to this file. It is never shipped by setup.py, so updates do not overwrite it.
if exist "%~dp0flow_env.local.bat" call "%~dp0flow_env.local.bat"
