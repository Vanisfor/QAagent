$ErrorActionPreference = "Stop"
$frontendRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $frontendRoot

$vite = Join-Path $frontendRoot "node_modules\.bin\vite.cmd"
if (-not (Test-Path -LiteralPath $vite)) {
    throw "Frontend dependencies are missing. Run pnpm install in $frontendRoot first."
}

Write-Host "QA Agent frontend: http://localhost:3002" -ForegroundColor Green
& $vite --host 127.0.0.1 --port 3002 --strictPort
