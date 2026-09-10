# Launches everything: Decaid, then the portal + logger, then Edge in kiosk
# mode pointed at the portal. Meant to run at Windows startup and from the
# desktop shortcut (both created by install-shortcuts.ps1) - safe to run
# again any time (e.g. after a crash) without a reboot.

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$DecaidExe = "C:\Program Files\Decaid\decaid.exe"
$PortalUrl = "http://localhost:5000"
$DecaidApiUrl = "http://localhost:8080/api/v1/machine/info"
$LogDir = Join-Path $RepoRoot "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Wait-ForUrl {
    param([string]$Url, [int]$TimeoutSeconds)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3 | Out-Null
            return $true
        } catch {
            Start-Sleep -Seconds 2
        }
    }
    return $false
}

# 1. Decaid
if (-not (Get-Process -Name "decaid" -ErrorAction SilentlyContinue)) {
    if (Test-Path $DecaidExe) {
        Write-Host "Starting Decaid..."
        Start-Process -FilePath $DecaidExe
    } else {
        Write-Warning "Decaid.exe not found at $DecaidExe - skipping, start it manually."
    }
} else {
    Write-Host "Decaid is already running."
}

Write-Host "Waiting for Decaid's API..."
if (-not (Wait-ForUrl -Url $DecaidApiUrl -TimeoutSeconds 60)) {
    Write-Warning "Decaid's API didn't respond within 60s - continuing anyway, the portal will retry on its own."
}

# 2. Portal + logger, each backgrounded with output going to a log file
# instead of a console window (nothing to look at on a kiosk anyway).
Write-Host "Starting the portal..."
Start-Process -FilePath ".\.venv\Scripts\python.exe" -ArgumentList "run_portal.py" `
    -WorkingDirectory $RepoRoot -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $LogDir "portal.log") `
    -RedirectStandardError (Join-Path $LogDir "portal.err.log")

Write-Host "Starting the logger..."
Start-Process -FilePath ".\.venv\Scripts\python.exe" -ArgumentList "run_logger.py" `
    -WorkingDirectory $RepoRoot -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $LogDir "logger.log") `
    -RedirectStandardError (Join-Path $LogDir "logger.err.log")

Write-Host "Waiting for the portal to come up..."
Wait-ForUrl -Url $PortalUrl -TimeoutSeconds 30 | Out-Null

# 3. Edge in kiosk mode (fullscreen, no address bar, no way to navigate away)
Write-Host "Launching Edge in kiosk mode..."
Start-Process -FilePath "msedge.exe" -ArgumentList "--kiosk", $PortalUrl, "--edge-kiosk-type=fullscreen", "--no-first-run"
