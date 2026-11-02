$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Create the Python environment first. See README.md.'
}
if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'dist\index.html'))) {
    npm run build
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
& $pythonPath -m backend.launcher

