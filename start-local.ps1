$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$port = 5000
if (Test-Path '.env') {
    $portLine = Get-Content '.env' | Where-Object { $_ -match '^PORT=\d+\s*$' } | Select-Object -Last 1
    if ($portLine) { $port = [int]($portLine.Split('=')[1].Trim()) }
}
$address = "http://127.0.0.1:$port"
function Test-App {
    try {
        $result = Invoke-RestMethod "$address/health" -TimeoutSec 2
        return ($result.status -eq 'ok' -and $null -ne $result.scraper_backend)
    } catch { return $false }
}
if (-not (Test-App)) {
    $python = Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe'
    if (-not (Test-Path -LiteralPath $python)) { throw 'Python environment missing. Follow README installation steps first.' }
    New-Item -ItemType Directory -Path 'data' -Force | Out-Null
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
    Start-Process -FilePath $python -ArgumentList 'run.py' -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $PSScriptRoot "data/launcher-$stamp.out.log") -RedirectStandardError (Join-Path $PSScriptRoot "data/launcher-$stamp.err.log") | Out-Null
    $ready = $false
    for ($attempt=0; $attempt -lt 20; $attempt++) {
        if (Test-App) { $ready=$true; break }
        Start-Sleep -Seconds 1
    }
    if (-not $ready) { throw 'The app did not start. Check data/app.log and the latest launcher error log.' }
}
Write-Output "PP Links is ready at $address"
Start-Process $address
