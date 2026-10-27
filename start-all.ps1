Write-Host "Starting NotebookE (Database + API + Worker + Frontend)..."
Write-Host "Starting SurrealDB..."
docker compose -f docker-compose.yml up -d surrealdb

Start-Sleep -Seconds 3
if (Test-Path '.codex-runtime/VieNeu-TTS/.venv/Scripts/python.exe') {
    $vieNeuListening = Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue
    if (-not $vieNeuListening) {
        Write-Host "Starting VieNeu speech server on port 8001..."
        Start-Process powershell -WindowStyle Hidden -ArgumentList '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', '.\scripts\start-vieneu.ps1' -RedirectStandardOutput '.codex-runtime/vieneu.out.log' -RedirectStandardError '.codex-runtime/vieneu.err.log'
    }
}
Write-Host "Starting API backend in a new window..."
Start-Process powershell -ArgumentList "-NoExit", "-Command", "`$env:Path = '$HOME\.local\bin;' + `$env:Path; chcp 65001; `$env:PYTHONUTF8=1; uv run --env-file .env run_api.py"

Start-Sleep -Seconds 3
Write-Host "Starting background worker in a new window..."
Start-Process powershell -ArgumentList "-NoExit", "-Command", "`$env:Path = '$HOME\.local\bin;' + `$env:Path; chcp 65001; `$env:PYTHONUTF8='1'; `$env:PYTHONIOENCODING='utf-8'; uv run --env-file .env surreal-commands-worker start --import-modules commands --max-tasks 5"

Start-Sleep -Seconds 2
Write-Host "Starting Next.js frontend in a new window..."
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd frontend; npm run dev"

Write-Host "All services started in separate windows!"
Write-Host "Frontend: http://localhost:3000"
Write-Host "API: http://localhost:5055"
Write-Host "API Docs: http://localhost:5055/docs"
