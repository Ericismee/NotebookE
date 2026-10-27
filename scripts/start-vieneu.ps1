param([int]$Port = 8001)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$runtimeRoot = Join-Path $projectRoot '.codex-runtime'
$serverRoot = Join-Path $runtimeRoot 'VieNeu-TTS'
$pythonPath = Join-Path $serverRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'VieNeu runtime is not installed. See docs/5-CONFIGURATION/vieneu.md.'
}
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:HF_HOME = Join-Path $runtimeRoot 'huggingface'
$env:HOST = '127.0.0.1'
$env:PORT = "$Port"
$env:VIENEU_BACKEND = 'onnx'
$env:VIENEU_DEVICE = 'cpu'
$env:VIENEU_MAX_STREAMS = '1'
$env:VIENEU_QUEUE = '32'
$env:VIENEU_QUEUE_TIMEOUT = '600'
Push-Location $serverRoot
try {
    & $pythonPath -m apps.openai_speech
} finally {
    Pop-Location
}
