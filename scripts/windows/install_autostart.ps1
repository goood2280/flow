<#
Flow auto-start on Windows (Task Scheduler).

Registers a scheduled task "FlowWebApp" that runs scripts\windows\flow_run.bat
at system startup (no login needed). flow_run.bat keeps uvicorn running and
restarts it whenever it exits; Task Scheduler also restarts the runner itself
if it ever stops.

Run from an elevated PowerShell (Run as administrator):

  powershell -ExecutionPolicy Bypass -File .\scripts\windows\install_autostart.ps1 -StartNow
  powershell -ExecutionPolicy Bypass -File .\scripts\windows\install_autostart.ps1 -StartNow -OpenFirewall -DisableSleep
  powershell -ExecutionPolicy Bypass -File .\scripts\windows\install_autostart.ps1 -Uninstall

Options
  -StartNow       start the task right after registering
  -OpenFirewall   allow inbound TCP on the Flow port (default 8080)
  -DisableSleep   keep the server awake on AC power (no standby / hibernate)
  -RunAsUser      run as the current user at logon instead of SYSTEM at boot
  -Port <n>       port for the firewall rule (default 8080)
  -Uninstall      remove the task and firewall rule
#>
param(
    [switch]$StartNow,
    [switch]$OpenFirewall,
    [switch]$DisableSleep,
    [switch]$RunAsUser,
    [switch]$Uninstall,
    [int]$Port = 8080,
    [string]$TaskName = "FlowWebApp"
)

$ErrorActionPreference = "Stop"
$runner = Join-Path $PSScriptRoot "flow_run.bat"
$appDir = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$ruleName = "Flow Web App $Port"

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-Admin)) {
    Write-Error "Run this script from an elevated PowerShell (Run as administrator)."
    exit 1
}

if ($Uninstall) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Removed scheduled task $TaskName"
    }
    if (Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue) {
        Remove-NetFirewallRule -DisplayName $ruleName
        Write-Host "Removed firewall rule $ruleName"
    }
    exit 0
}

if (-not (Test-Path $runner)) {
    Write-Error "flow_run.bat not found next to this script: $runner"
    exit 1
}

# SYSTEM does not see a per-user Python on PATH, so pin the full path now.
# Priority: -Python argument > $env:PYTHON_EXE > active conda/miniforge env > python on PATH.
# Run this script from the Miniforge Prompt after 'conda activate <env>' to pin that env.
$python = $Python
if (-not $python -and $env:PYTHON_EXE) { $python = $env:PYTHON_EXE }
if (-not $python -and $env:CONDA_PREFIX -and (Test-Path (Join-Path $env:CONDA_PREFIX "python.exe"))) {
    $python = Join-Path $env:CONDA_PREFIX "python.exe"
}
if (-not $python) { $python = (Get-Command python -ErrorAction SilentlyContinue).Source }
if (-not $python) {
    Write-Error "python was not found. Activate the Flow conda env (conda activate flow) or pass -Python <full path to python.exe>."
    exit 1
}
if ($python -like "*\WindowsApps\*") {
    Write-Error "$python is the Microsoft Store alias; SYSTEM cannot run it. Pass -Python with the real python.exe (e.g. C:\ProgramData\miniforge3\envs\flow\python.exe)."
    exit 1
}
& $python -c "import uvicorn, fastapi, polars, psutil" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Error "uvicorn/fastapi/polars/psutil are not importable with $python. Run 'python setup.py install-deps' in $appDir with that Python first."
    exit 1
}
if ($python -like "$env:USERPROFILE*" -and -not $RunAsUser) {
    Write-Warning "$python is inside a user profile. SYSTEM can usually read it, but an all-users install (C:\ProgramData\miniforge3) is safer for a boot-time task."
}

$cmdArgs = "/c set ""PYTHON_EXE=$python""&& ""$runner"""
$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument $cmdArgs -WorkingDirectory $appDir

if ($RunAsUser) {
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest
} else {
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
}

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings `
    -Description "Flow web app (uvicorn app:app :$Port) with automatic restart" | Out-Null
Write-Host "Registered scheduled task $TaskName"
Write-Host "  runner : $runner"
Write-Host "  python : $python"
Write-Host "  app dir: $appDir"

if ($OpenFirewall) {
    if (-not (Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Protocol TCP -LocalPort $Port -Action Allow | Out-Null
    }
    Write-Host "Firewall: inbound TCP $Port allowed"
}

if ($DisableSleep) {
    powercfg /change standby-timeout-ac 0
    powercfg /change hibernate-timeout-ac 0
    powercfg /hibernate off
    Write-Host "Power: standby and hibernate disabled on AC power"
}

if ($StartNow) {
    Start-ScheduledTask -TaskName $TaskName
    Write-Host "Started $TaskName - check http://localhost:$Port in a few seconds"
}
