param([int]$Port = 8002, [switch]$SeedDemo)

$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) {
    $taskPython = Join-Path $taskRoot '..\.tools\python312\python.exe'
}
if (-not (Test-Path -LiteralPath $taskPython)) { $taskPython = 'python' }
Push-Location $taskRoot
try {
    & $taskPython -c 'from app.config.settings import Settings; s = Settings(); raise SystemExit(0 if s.llm_configured else "Configure DEEPSEEK_API_KEY in the project .env first.")'
    if ($LASTEXITCODE -ne 0) { throw 'Online model configuration is missing.' }
    if ($SeedDemo) {
        & $taskPython -m scripts.seed_demo
        if ($LASTEXITCODE -ne 0) { throw 'Demo data initialization failed.' }
    }
    & $taskPython -m uvicorn app.asgi:app --host 127.0.0.1 --port $Port
} finally { Pop-Location }
