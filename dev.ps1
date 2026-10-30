param([ValidateSet('setup', 'up', 'down', 'api', 'health', 'trigger', 'nlp', 'spatial', 'analytics', 'scout', 'assistant', 'eval', 'migrate', 'test', 'lint', 'ui', 'demo', 'summary')]
    [string]$Task = 'health')
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    $pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    switch ($Task) {
        'setup' {
            if (!(Test-Path -LiteralPath $pythonPath)) {
                if (Get-Command py -ErrorAction SilentlyContinue) {
                    py -3.11 -m venv .venv
                } elseif (Get-Command python -ErrorAction SilentlyContinue) {
                    python -c "import sys; assert sys.version_info[:2] == (3, 11), 'Use Python 3.11'"
                    if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.11, then rerun setup.' }
                    python -m venv .venv
                } else { throw 'Install Python 3.11 with the Windows Python launcher, then rerun setup.' }
                if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 virtual environment creation failed.' }
            }
            & $pythonPath -c "import sys; assert sys.version_info[:2] == (3, 11), 'Use Python 3.11'"
            if ($LASTEXITCODE -ne 0) { throw 'The existing .venv must use Python 3.11.' }
            & $pythonPath -m pip install -r requirements-dev.txt
            if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
            if (!(Test-Path -LiteralPath '.env')) { Copy-Item .env.example .env }
        }
        'up' { docker compose up -d --wait }
        'down' { docker compose down }
        'api' { & $pythonPath -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000 }
        'health' { Invoke-RestMethod 'http://127.0.0.1:8000/health' | ConvertTo-Json -Depth 6 }
        'trigger' { Invoke-RestMethod -Method Post 'http://127.0.0.1:8000/api/v1/ingest/trigger' | ConvertTo-Json -Depth 6 }
        'nlp' { Invoke-RestMethod -Method Post 'http://127.0.0.1:8000/api/v1/nlp/process' | ConvertTo-Json -Depth 6 }
        'spatial' { Invoke-RestMethod -Method Post 'http://127.0.0.1:8000/api/v1/spatial/process' | ConvertTo-Json -Depth 6 }
        'analytics' { Invoke-RestMethod -Method Post 'http://127.0.0.1:8000/api/v1/analytics/process' | ConvertTo-Json -Depth 6 }
        'scout' {
            $scoutBody = @{ origin = 'Clock Tower'; destination = 'Laxman Jhula'; include_fixtures = $true } | ConvertTo-Json
            Invoke-RestMethod -Method Post 'http://127.0.0.1:8000/api/v1/scout/evaluate' -ContentType 'application/json' -Body $scoutBody | ConvertTo-Json -Depth 12
        }
        'assistant' {
            $assistantBody = @{ query = 'Are there active accidents near Clock Tower?'; include_fixtures = $true } | ConvertTo-Json
            Invoke-RestMethod -Method Post 'http://127.0.0.1:8000/api/v1/assistant/query' -ContentType 'application/json' -Body $assistantBody | ConvertTo-Json -Depth 12
        }
        'eval' { & $pythonPath -m backend.nlp.evaluate }
        'ui' {
            Push-Location (Join-Path $PSScriptRoot 'frontend')
            try {
                npm install
                if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
                npm run dev
            } finally { Pop-Location }
        }
        'demo' { & $pythonPath -m backend.streaming.demo_pipeline }
        'summary' { & $pythonPath -m backend.evaluation.summary }
        'migrate' {
            docker compose exec -T trafficsense-postgis sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f /docker-entrypoint-initdb.d/migrate.psql'
        }
        'test' { & $pythonPath -m pytest -q }
        'lint' { & $pythonPath -m ruff check backend tests scripts training/train_setfit.py }
    }
    if ($Task -notin @('health', 'trigger', 'nlp', 'spatial', 'analytics', 'scout', 'assistant') -and $LASTEXITCODE -ne 0) { throw "Task '$Task' failed." }
} finally { Pop-Location }
