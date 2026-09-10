# One-time setup: creates a "Start espressolab" shortcut on the Desktop and
# an identical one in the Startup folder (so it also runs automatically at
# login). Both just run start-all.ps1 - safe to re-run this installer any
# time, it overwrites the same two shortcuts rather than duplicating them.

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$StartAllScript = Join-Path $RepoRoot "scripts\start-all.ps1"

if (-not (Test-Path $StartAllScript)) {
    throw "Can't find $StartAllScript"
}

$WshShell = New-Object -ComObject WScript.Shell

function New-EspressolabShortcut {
    param([string]$Path)
    $shortcut = $WshShell.CreateShortcut($Path)
    $shortcut.TargetPath = "powershell.exe"
    $shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$StartAllScript`""
    $shortcut.WorkingDirectory = $RepoRoot
    $shortcut.WindowStyle = 7  # minimized
    $shortcut.IconLocation = "imageres.dll,76"  # a cup-ish icon from the built-in Windows icon set
    $shortcut.Save()
}

$DesktopShortcut = Join-Path ([Environment]::GetFolderPath("Desktop")) "Start espressolab.lnk"
New-EspressolabShortcut -Path $DesktopShortcut
Write-Host "Created: $DesktopShortcut"

$StartupShortcut = Join-Path ([Environment]::GetFolderPath("Startup")) "Start espressolab.lnk"
New-EspressolabShortcut -Path $StartupShortcut
Write-Host "Created: $StartupShortcut"

Write-Host "Done. espressolab will now start automatically at login, and the desktop icon re-runs the same thing on demand."
