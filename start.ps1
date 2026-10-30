# @file: start.ps1
# @exports: One-command Windows development launcher; -NoBrowser is optional
# @depends_on: dev.ps1, Python 3.11, Node 22.12+, Docker Compose
# @purpose: Set up a clone, start local services, and stop only owned app processes on exit.
param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$taskRoot = $PSScriptRoot
$taskProcesses = @()
Push-Location $taskRoot
try {
    foreach ($taskCommand in @('docker', 'node', 'npm')) {
        if (!(Get-Command $taskCommand -ErrorAction SilentlyContinue)) {
            throw "Missing $taskCommand. Install the prerequisites listed in README.md."
        }
    }
    node -e "const [a,b]=process.versions.node.split('.').map(Number); process.exit(a>22||(a===22&&b>=12)?0:1)"
    if ($LASTEXITCODE -ne 0) { throw 'Use Node.js 24 LTS or Node.js 22.12+.' }
    docker info --format '{{.ServerVersion}}' 2>$null
    if ($LASTEXITCODE -ne 0) { throw 'Start Docker Desktop (Linux containers), then rerun .\start.ps1.' }
    docker compose version
    if ($LASTEXITCODE -ne 0) { throw 'Docker Compose v2 or newer is required.' }
    foreach ($taskPort in @(8000, 5173)) {
        $taskListener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $taskPort)
        try { $taskListener.Start() }
        catch { throw "Port $taskPort is occupied. Stop the existing app before starting this launcher." }
        finally { $taskListener.Stop() }
    }
    & (Join-Path $taskRoot 'dev.ps1') setup
    Push-Location (Join-Path $taskRoot 'frontend')
    try {
        npm ci
        if ($LASTEXITCODE -ne 0) { throw 'Frontend installation failed. Check your internet connection.' }
    } finally { Pop-Location }
    & (Join-Path $taskRoot 'dev.ps1') up
    & (Join-Path $taskRoot 'dev.ps1') migrate
    $taskLogDir = Join-Path $taskRoot 'logs'
    New-Item -ItemType Directory -Path $taskLogDir -Force | Out-Null
    $taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
    $taskNode = (Get-Command node).Source
    $taskApi = Start-Process -FilePath $taskPython -ArgumentList @(
        '-m', 'uvicorn', 'backend.main:app', '--host', '127.0.0.1', '--port', '8000'
    ) -WorkingDirectory $taskRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $taskLogDir 'api.stdout.log') `
        -RedirectStandardError (Join-Path $taskLogDir 'api.stderr.log')
    $taskProcesses += $taskApi
    $taskUi = Start-Process -FilePath $taskNode -ArgumentList @(
        'node_modules/vite/bin/vite.js', '--host', '127.0.0.1', '--port', '5173', '--strictPort'
    ) -WorkingDirectory (Join-Path $taskRoot 'frontend') -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $taskLogDir 'ui.stdout.log') `
        -RedirectStandardError (Join-Path $taskLogDir 'ui.stderr.log')
    $taskProcesses += $taskUi
    $taskReady = $false
    for ($taskAttempt = 0; $taskAttempt -lt 30; $taskAttempt++) {
        if ($taskApi.HasExited -or $taskUi.HasExited) { throw "An app exited. Inspect logs in $taskLogDir." }
        try {
            $taskHealth = Invoke-RestMethod 'http://127.0.0.1:8000/health' -TimeoutSec 2
            $taskPage = Invoke-WebRequest 'http://127.0.0.1:5173' -UseBasicParsing -TimeoutSec 2
            if ($taskHealth.status -eq 'ok' -and $taskPage.StatusCode -eq 200) { $taskReady = $true; break }
        } catch { Start-Sleep -Seconds 1 }
    }
    if (!$taskReady) { throw "Apps did not become healthy. Inspect logs in $taskLogDir." }
    Write-Host 'TrafficSense is ready at http://127.0.0.1:5173'
    Write-Host 'API documentation: http://127.0.0.1:8000/docs'
    Write-Host 'Use Run full fixture pipeline in the console drawer to load sample incidents.'
    Write-Host 'Keep this window open; Ctrl+C stops the apps.'
    Write-Host 'Database containers persist. Run .\dev.ps1 down when finished to stop them without deleting data.'
    if (!$NoBrowser) { Start-Process 'http://127.0.0.1:5173' }
    while (!$taskApi.HasExited -and !$taskUi.HasExited) { Start-Sleep -Seconds 1 }
    throw "An app stopped. Inspect logs in $taskLogDir."
} finally {
    foreach ($taskProcess in $taskProcesses) {
        if (!$taskProcess.HasExited) { Stop-Process -Id $taskProcess.Id -ErrorAction SilentlyContinue }
    }
    Pop-Location
}
