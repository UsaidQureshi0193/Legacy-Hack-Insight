# Start the Prefect server and the hourly flow in two separate windows.
# Run this from YOUR OWN PowerShell (not from an editor/AI tool) and keep both windows open
# from Tue 20 Oct 4 PM to Wed 21 Oct 4 PM PKT. Laptop: plugged in, sleep disabled.
#
#   powershell -ExecutionPolicy Bypass -File scripts\start_hourly.ps1
$root = Split-Path -Parent $PSScriptRoot
$prefect = Join-Path $root ".venv\Scripts\prefect.exe"
$python = Join-Path $root ".venv\Scripts\python.exe"

# 1) Prefect server (keeps the run history in ~/.prefect, needed for prefect_runs.csv)
Start-Process powershell -WorkingDirectory $root -ArgumentList "-NoExit", "-Command",
    "`$env:PYTHONUTF8='1'; & '$prefect' server start --host 127.0.0.1 --port 4200"

# wait until the server answers
$ok = $false
for ($i = 0; $i -lt 60 -and -not $ok; $i++) {
    try { $ok = (Invoke-RestMethod "http://127.0.0.1:4200/api/health" -TimeoutSec 3) -eq $true } catch { Start-Sleep 3 }
}
if (-not $ok) { Write-Error "Prefect server did not start"; exit 1 }

# 2) hourly flow (cron 5 * * * *)
Start-Process powershell -WorkingDirectory $root -ArgumentList "-NoExit", "-Command",
    "`$env:PYTHONUTF8='1'; `$env:HF_HUB_DISABLE_SYMLINKS_WARNING='1'; `$env:PREFECT_API_URL='http://127.0.0.1:4200/api'; & '$python' -m hackinsight.pipeline.flow"

Write-Host "Started. Prefect UI: http://127.0.0.1:4200  (keep both windows open)"
