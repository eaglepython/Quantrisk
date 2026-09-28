$ErrorActionPreference = 'Stop'

$repo = 'C:\Users\garde\Desktop\quantrisk'
$python = Join-Path $repo '.venv\Scripts\python.exe'
$asOf = (Get-Date).ToString('yyyy-MM-dd')

Set-Location $repo

if (-not (Test-Path $python)) {
    throw "Python venv not found at $python"
}

if ($env:FRED_API_KEY) {
    Write-Host "FRED_API_KEY detected - running live production daily cycle"
    & $python -m quantrisk.cli daily --as-of $asOf --no-ai
} else {
    Write-Host "FRED_API_KEY not detected - running sample-data daily cycle"
    & $python -m quantrisk.cli run --as-of $asOf --with-sample
}

if ($LASTEXITCODE -ne 0) {
    throw "Daily run failed with exit code $LASTEXITCODE"
}
