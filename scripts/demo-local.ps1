param([int]$Port = 8002, [switch]$SeedDemo)

$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) {
    $taskPython = Join-Path $taskRoot '..\.tools\python312\python.exe'
}
if (-not (Test-Path -LiteralPath $taskPython)) {
    $taskPython = 'python'
}
& $taskPython -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11+ is required"'
if ($LASTEXITCODE -ne 0) { throw 'A Python 3.11+ environment is required.' }

$taskEnvNames = @('LOCAL_EMBEDDING_ENABLED', 'CROSS_ENCODER_ENABLED', 'UNSTRUCTURED_ENABLED', 'DEEPSEEK_API_KEY', 'EMBEDDING_API_KEY', 'REDIS_URL', 'QDRANT_URL', 'APP_ENV')
$taskSavedEnv = @{}
foreach ($taskName in $taskEnvNames) {
    $taskSavedEnv[$taskName] = [Environment]::GetEnvironmentVariable($taskName, 'Process')
}
$env:LOCAL_EMBEDDING_ENABLED = 'false'
$env:CROSS_ENCODER_ENABLED = 'false'
$env:UNSTRUCTURED_ENABLED = 'false'
$env:DEEPSEEK_API_KEY = ''
$env:EMBEDDING_API_KEY = ''
$env:REDIS_URL = ''
$env:QDRANT_URL = ''
$env:APP_ENV = 'development'
Push-Location $taskRoot
try {
    if ($SeedDemo) {
        & $taskPython -m scripts.seed_demo
        if ($LASTEXITCODE -ne 0) { throw 'Demo data initialization failed.' }
    }
    Write-Host "Local API: http://127.0.0.1:$Port/docs"
    Write-Host 'Offline evidence mode; no model downloads or external API calls.'
    & $taskPython -m uvicorn app.asgi:app --host 127.0.0.1 --port $Port
} finally {
    foreach ($taskName in $taskEnvNames) {
        [Environment]::SetEnvironmentVariable($taskName, $taskSavedEnv[$taskName], 'Process')
    }
    Pop-Location
}
