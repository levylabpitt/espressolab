$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
py -3 -m venv .venv
& .\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Write-Host "Done. Copy .env.example to .env and fill in DATABASE_URL etc, then run scripts\start-portal.ps1 / start-logger.ps1"
