# Windows helper with the same targets as the Makefile:  .\make.ps1 data
param([Parameter(Mandatory = $true)][string]$Target)
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$env:PYTHONUTF8 = "1"
switch ($Target) {
    "setup"       { python -m uv venv --python 3.12 .venv; python -m uv pip install --python $py -e . }
    "data"        { & $py -m hackinsight.cli data }
    "update"      { & $py -m hackinsight.cli update }
    "train"       { & $py -m hackinsight.cli train }
    "index"       { & $py -m hackinsight.cli index }
    "all"         { & $py -m hackinsight.cli data; & $py -m hackinsight.cli train; & $py -m hackinsight.cli index }
    "app"         { & $py app/app.py }
    "server"      { & (Join-Path $PSScriptRoot ".venv\Scripts\prefect.exe") server start --host 127.0.0.1 --port 4200 }
    "flow"        { $env:PREFECT_API_URL = "http://127.0.0.1:4200/api"; & $py -m hackinsight.pipeline.flow }
    "test"        { & $py -m pytest -q }
    "export-runs" { $env:PREFECT_API_URL = "http://127.0.0.1:4200/api"; & $py -m hackinsight.cli export-runs }
    default       { Write-Error "unknown target $Target" }
}
exit $LASTEXITCODE
