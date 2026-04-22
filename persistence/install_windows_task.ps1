# GLOBE-CLI — Windows Task Scheduler Auto-Restart
# Run as Administrator: powershell -ExecutionPolicy Bypass -File install_windows_task.ps1

$TaskName = "GLOBE-CLI-Server"
$ProjectDir = Split-Path -Parent $PSScriptRoot  # parent of persistence/
$PythonExe = Join-Path $ProjectDir ".venv\Scripts\python.exe"
$ServerScript = Join-Path $ProjectDir "server.py"

# Validate paths
if (-not (Test-Path $PythonExe)) {
    Write-Host "ERROR: Python not found at $PythonExe" -ForegroundColor Red
    Write-Host "Run setup.py first to create the virtual environment." -ForegroundColor Yellow
    exit 1
}

if (-not (Test-Path $ServerScript)) {
    Write-Host "ERROR: server.py not found at $ServerScript" -ForegroundColor Red
    exit 1
}

# Remove existing task if present
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Removing existing task '$TaskName'..." -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

# Create the action
$Action = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument "`"$ServerScript`"" `
    -WorkingDirectory $ProjectDir

# Triggers: at logon + at startup
$TriggerLogon = New-ScheduledTaskTrigger -AtLogOn
$TriggerBoot = New-ScheduledTaskTrigger -AtStartup

# Settings: restart on failure, run indefinitely
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Seconds 10) `
    -ExecutionTimeLimit (New-TimeSpan -Days 365)

# Register the task
Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $TriggerLogon, $TriggerBoot `
    -Settings $Settings `
    -Description "GLOBE-CLI AI Server — Auto-restart on crash/reboot" `
    -RunLevel Highest

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  GLOBE-CLI Task Scheduled Successfully" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Task Name:   $TaskName"
Write-Host "  Python:      $PythonExe"
Write-Host "  Server:      $ServerScript"
Write-Host "  Triggers:    At Logon + At Startup"
Write-Host "  Restart:     Every 10s on failure"
Write-Host ""
Write-Host "  Start now:   Start-ScheduledTask -TaskName '$TaskName'" -ForegroundColor Yellow
Write-Host "  Stop:        Stop-ScheduledTask -TaskName '$TaskName'" -ForegroundColor Yellow
Write-Host "  Remove:      Unregister-ScheduledTask -TaskName '$TaskName'" -ForegroundColor Yellow
